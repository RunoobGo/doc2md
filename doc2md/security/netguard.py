"""NFR-1 离线断言（`run.offline_assert`，CH-456 / T9 落地）。

主链路零出站网络是 Doc2md 的定位红线（方案 1.1 / NFR-1）。在断言开启时，
转换过程内任何指向非回环地址的连接尝试立即抛 `OfflineViolation`——
把「依赖清单干净」这条纪律从声明变成运行期可判定事实。

CH-469（O-1）三处收口：
- **重入/并发**：补丁改为「引用计数 + 全局唯一分发器」。原实现每次进入都读一遍
  `socket.socket.connect` 再在退出时无条件写回，两个线程（或嵌套 with）交叠时，
  后进入者会把**上一层的守护函数**当作 original 记下，先退出者把真实实现写回，
  后退出者再把守护函数装回去 → 守护永久残留在进程里（服务面后续所有出站连接
  全被拦），或反向出现「守护还在、补丁已被摘」的空窗。
- **绕过面**：`connect_ex`（非阻塞 connect，语义等价）、数据报的 `sendto` 与 `sendmsg`
  同样是出站原语，原实现只拦 `connect` → 一个 `connect_ex` 就绕开红线。CH-471 SEC-1 补齐
  `sendmsg`（`sendto` 之外的第二条数据报发送通道）。
- **允许清单**：`localhost` 原先按**字面名**放行。hosts 投毒 / 异常 DNS 把
  `localhost` 指到公网地址时，名字匹配等于没匹配；现在凡不在字面 IP 白名单里的
  主机名，一律解析后逐个判定是否回环（解析失败按拒绝）。

边界（如实记录，方案 12.7 口径）：
- 守护在**进程内**替换 socket 层的连接原语，对 `convert()` 调用区间生效；
  批量场景由 spawn 子进程各自重新施加（守护写在 convert 入口，不依赖继承）。
- 多线程服务面（`doc2md serve`）下补丁是全局的：守护期间其他线程的连接同样受检。
  这是「宁可误报也不漏报」的取舍，误报面仅限同期真的在连外网。
- CH-471（SEC-2）：对非字面 IP 的**主机名**，`_allowed` 需先 `getaddrinfo` 解析才能判定
  是否回环——这一步会向系统解析器发一条 DNS 查询（即一次出站），随后才拦下 TCP 连接。
  真离线机上解析失败即判「不可达→拒绝」，失败关闭；联网机上则把被拦的主机名泄给本地
  DNS。因主链路只连 loopback（`llm.bind`/`api.bind`），实际不触发名字解析；如需绝对
  不解析，可把目标一律写成 IP 字面量，或对未知主机名直接拒（未采，避免误伤 localhost）。
"""
from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading

ALLOWED_HOSTS = ("127.0.0.1", "::1", "localhost")   # 公开清单（向后兼容）；
                                       # 守护实际只按 IP 字面量放行，名字见 _allowed

_LOCK = threading.RLock()
_STATE = {"depth": 0, "allowed": set(), "resolvers": {}}   # allowed 在每次装桩时重算
_ORIG = {}


class OfflineViolation(RuntimeError):
    """出站连接被离线断言拦截（NFR-1）。"""


def _host_of(address) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _is_loopback_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed(host: str, allow: set[str]) -> bool:
    """字面命中 → 直通；否则解析后**全部**地址必须是回环。

    CH-469（O-1 收口）：`allow` 在 `_install` 里已过滤成「只有 IP 字面量」，
    所以 `host in allow` 不可能是主机名命中——`localhost` 这类名字一律走解析判定。
    原实现把 `localhost` 放进字面白名单，hosts 投毒 / 异常 DNS 把它指到公网时
    守护直接放行，等于这条红线按名字而非按地址生效。
    """
    if host in allow or _is_loopback_literal(host):
        return True
    if host in ("", "0.0.0.0", "::"):          # 未指定地址：不是「连出去」，但按最严处理
        return False
    cache = _STATE["resolvers"]
    infos = cache.get(host)
    if infos is None:
        try:
            infos = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
        except OSError:
            infos = frozenset()                # 解析失败 = 不可判定 = 拒绝
        cache[host] = infos
    return bool(infos) and all(_is_loopback_literal(ip) for ip in infos)


def _check(address) -> None:
    host = _host_of(address)
    if not _allowed(host, _STATE["allowed"]):
        raise OfflineViolation(
            f"NFR-1 offline assert: outbound connection to {host!r} blocked")


def _guarded_connect(self, address):
    _check(address)
    return _ORIG["connect"](self, address)


def _guarded_connect_ex(self, address):
    _check(address)
    return _ORIG["connect_ex"](self, address)


def _guarded_sendto(self, data, *args, **kw):
    # `sendto(bytes, address)` 与 `sendto(bytes, flags, address)` 两种形参都要覆盖；
    # 已连接的数据报套接字不传地址（出站面在 connect 上已经判过）。
    addr = kw.get("address")
    if addr is None:
        for cand in reversed(args):
            if not isinstance(cand, int):
                addr = cand
                break
    if addr is not None:
        _check(addr)
    return _ORIG["sendto"](self, data, *args, **kw)


def _sendmsg_address(args: tuple, kw: dict):
    """从 sendmsg 的调用形参里取出目标地址（无则 None）。

    签名 `sendmsg(message[, ancdata, flags, address])`——address 既可能是第 3 个可选
    位置参（args[2]），也可能同名关键字；其余 args（ancdata / flags）不是地址。
    """
    addr = kw.get("address")
    if addr is None and len(args) >= 3:
        addr = args[2]
    return addr


def _guarded_sendmsg(self, message, *args, **kw):
    # CH-471 SEC-1：`sendmsg` 是 `sendto` 之外的另一条数据报出站原语，未打补丁即可绕过红线。
    # 连接态套接字用占位 (0, 0) 表示「走已连接的对端」，此时不重复判定（出站面在 connect 已判）。
    addr = _sendmsg_address(args, kw)
    if addr is not None and addr != (0, 0):
        _check(addr)
    return _ORIG["sendmsg"](self, message, *args, **kw)


def _install(allowed: set[str]) -> None:
    _ORIG["connect"] = socket.socket.connect
    _ORIG["connect_ex"] = socket.socket.connect_ex
    _ORIG["sendto"] = socket.socket.sendto
    _ORIG["sendmsg"] = socket.socket.sendmsg
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.socket.sendto = _guarded_sendto
    socket.socket.sendmsg = _guarded_sendmsg


def _uninstall() -> None:
    socket.socket.connect = _ORIG.pop("connect")
    socket.socket.connect_ex = _ORIG.pop("connect_ex")
    socket.socket.sendto = _ORIG.pop("sendto")
    socket.socket.sendmsg = _ORIG.pop("sendmsg")
    _ORIG.clear()
    _STATE["resolvers"].clear()


@contextlib.contextmanager
def offline_guard(enabled: bool = True, allowed: tuple[str, ...] = ALLOWED_HOSTS):
    """转换期离线守护；`enabled=False` 时为零成本直通（配置关断言即关守护）。

    可重入、可跨线程交叠：只有最外层退出时才摘补丁。
    """
    if not enabled:
        yield
        return
    with _LOCK:
        if _STATE["depth"] == 0:
            # 只保留 IP 字面量：主机名不进白名单（判定口径 = 地址，不是名字）
            _STATE["allowed"] = {a for a in (set(allowed) | set(ALLOWED_HOSTS))
                                 if _is_loopback_literal(a)}
            _STATE["resolvers"].clear()
            _install(_STATE["allowed"])
        _STATE["depth"] += 1
    try:
        yield
    finally:
        with _LOCK:
            _STATE["depth"] -= 1
            if _STATE["depth"] <= 0:
                _STATE["depth"] = 0
                _uninstall()


def is_armed() -> bool:
    """当前进程是否处于守护期（测试与自检用；不改变行为）。"""
    with _LOCK:
        return _STATE["depth"] > 0
