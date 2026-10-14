"""NFR-1 离线断言（`run.offline_assert`，CH-456 / T9 落地）。

主链路零出站网络是 Doc2md 的定位红线（方案 1.1 / NFR-1）。在断言开启时，
转换过程内任何指向非回环地址的连接尝试立即抛 `OfflineViolation`——
把「依赖清单干净」这条纪律从声明变成运行期可判定事实。

边界（如实记录，方案 12.7 口径）：
- 守护在**进程内**替换 `socket.socket.connect`，对 `convert()` 调用区间生效；
  批量场景由 spawn 子进程各自重新施加（守护写在 convert 入口，不依赖继承）。
- 多线程服务面（`doc2md serve`）下，补丁是全局的：守护期间其他线程的连接
  同样受检。这是「宁可误报也不漏报」的取舍，误报面仅限同期真的在连外网。
- 允许清单固定为回环：`127.0.0.1` / `::1` / `localhost`——本地修补助手
  （llama-server，T2）只连 127.0.0.1，不受影响。
"""
from __future__ import annotations

import contextlib
import socket

ALLOWED_HOSTS = ("127.0.0.1", "::1", "localhost")


class OfflineViolation(RuntimeError):
    """出站连接被离线断言拦截（NFR-1）。"""


def _host_of(address) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


@contextlib.contextmanager
def offline_guard(enabled: bool = True, allowed: tuple[str, ...] = ALLOWED_HOSTS):
    """转换期离线守护；`enabled=False` 时为零成本直通（配置关断言即关守护）。"""
    if not enabled:
        yield
        return
    original = socket.socket.connect
    allow = set(allowed)

    def _guarded(self, address):
        host = _host_of(address)
        if host not in allow:
            raise OfflineViolation(
                f"NFR-1 offline assert: outbound connection to {host!r} blocked")
        return original(self, address)

    socket.socket.connect = _guarded
    try:
        yield
    finally:
        socket.socket.connect = original
