#!/usr/bin/env python3
"""ops/bench/b14_crossfs_probe.py —— CH-499 / B-14 跨文件系统语义取证台。

B-14 的原有口径：`CH-483` 的 realpath containment、`security/perms.py` 的私有权限、
`security/names.py` 的消毒只在**本机默认 APFS 卷**上验过。这一台把同一组判定搬到
本机真能造出来的**其它文件系统**上跑，逐文件系统填表（方案 12.7 B-14 的出口判据）。
**取证轮**：默认不改产品；只有实测抓出缺陷才就地修并补 gate 红线。

覆盖的判定项（每项都打印 `fs=… item=… verdict=…` 便于回填表格）：

  perm      —— `ensure_private(0o600/0o700)` 后 `stat` 的 mode 位是否**真的**收紧、
               `is_private()` 判定结果、以及「装饰性权限位」（chmod 成功但 other 位仍在）
               这一必须暴露的语义；顺带看 `Store` 是否把不可表达告警吞掉。
  symlink   —— `os.symlink` 是否可用（FAT 系应失败）；不可用时 `is_link_like()`
               与 `guard_walk()` 的收敛姿态（不得把「造不出软链」误读成「防住了逃逸」）。
  case      —— 大小写敏感/不敏感（决定 `doc_id`/消毒名在同卷上是否互相覆盖）。
  names     —— `sanitize_name` + `assert_name_safe` 的产物在该卷上能否真实落盘并原样读回
               （非 ASCII、emoji、超长字节预算、Windows 保留名、尾点/空格）。
  store     —— `Store` 建库 + `journal_mode` + 一次真实 `doc2md convert`（产物 zip 与库面自洽）。

用法：
    python3 ops/bench/b14_crossfs_probe.py                      # 三种文件系统全试
    python3 ops/bench/b14_crossfs_probe.py --fs ExFAT
    python3 ops/bench/b14_crossfs_probe.py --keep               # 保留映像与挂载点

退出码：0=所有已造出的卷判据齐全（**不含**「产品缺陷」判定）；2=造卷失败或某项判据
无法采集；3=抓到产品级缺陷（抛异常/数据错写/库面不自洽），需登记为 A 类小修。

清理纪律：只动本夹具自己造的 `/tmp/b14_crossfs/*` 映像与挂载点；跑毕 `hdiutil detach`
并删映像，**绝不触碰用户已有的 /Volumes 内容**，`--keep` 时也只保留夹具目录。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC2MD = str(REPO / ".venv/bin/doc2md")
GOLDEN = REPO / "tests" / "golden"
# 目标文件系统：FAT32（最装饰性）、exFAT（移动介质常见）、大小写敏感 APFS（第三种语义）
FS_LIST = (("MS-DOS FAT32", "B14FAT32"), ("ExFAT", "B14EXFAT"),
           ("Case-sensitive APFS", "B14CSAPFS"))
IMG_BYTES = 1200 * 1024 * 1024     # 必须容得下 [perf].min_free_mb=512 的写入门禁


def _mount_opts(mnt: Path) -> str:
    """`mount` 里该卷那一行的括号段（noowners/fmode/dmode 决定合成权限长相）。"""
    for ln in _run(["mount"]).stdout.splitlines():
        if str(mnt) in ln or mnt.name in ln:
            return ln[ln.find("("):][:180]
    return "?"


def _console_safe() -> None:
    """CH-498 / B-16 口径：中文提示在非 UTF-8 控制台降级为 replace。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _run(argv: list[str], timeout: int = 180,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True,
                          errors="replace", timeout=timeout, env=env)


def say(fs: str, item: str, verdict: str, **extra) -> dict:
    row = {"fs": fs, "item": item, "verdict": verdict}
    row.update(extra)
    print(json.dumps(row, ensure_ascii=True), flush=True)
    return row


class Volume:
    """本夹具造的 DMG 映像 + 挂载点（生命周期严格由夹具管）。"""

    def __init__(self, base: Path, fs: str, vol: str):
        self.fs = fs
        self.vol = vol
        self.img = base / f"{vol}.dmg"
        self.mnt = base / "mnt" / vol
        self.mounted = False

    def create(self) -> str | None:
        if shutil.which("hdiutil") is None:
            return "hdiutil_missing"
        out = self.img.with_suffix("")          # hdiutil 补 .dmg 后缀
        _run(["hdiutil", "detach", str(self.mnt), "-force"])
        self.img.unlink(missing_ok=True)
        r = _run(["hdiutil", "create", "-size", f"{IMG_BYTES}", "-fs", self.fs,
                  "-volname", self.vol, str(out)])
        if r.returncode:
            return f"create_failed:{(r.stderr or r.stdout).strip()[:160]}"
        if not self.img.exists():
            cand = sorted(out.parent.glob(out.name + "*"))
            if not cand:
                return "create_no_image"
            self.img = cand[0]
        self.mnt.mkdir(parents=True, exist_ok=True)
        a = _run(["hdiutil", "attach", str(self.img), "-mountpoint", str(self.mnt),
                  "-nobrowse"])
        if a.returncode:
            return f"attach_failed:{(a.stderr or a.stdout).strip()[:160]}"
        self.mounted = True
        return None

    def detach(self) -> None:
        if self.mounted:
            _run(["hdiutil", "detach", str(self.mnt), "-force"])
            self.mounted = False
        self.img.unlink(missing_ok=True)

    def fs_type(self) -> str:
        """`mount` 行括号里的第一个选项即内核文件系统名（msdos / exfat / apfs）。

        macOS 的 `stat -f "%T"` 打的是十六进制魔数（不是 Linux 那种类型名字符串），
        第一轮取证因此拿到过废值；`mount` 的选项串另有 `_mount_opts` 用作挂载判据。
        """
        for ln in _run(["mount"]).stdout.splitlines():
            if str(self.mnt) in ln and "(" in ln:
                opts = ln[ln.find("(") + 1:].split(",")
                if opts:
                    return opts[0].strip()
        return "?"


def probe_perm(fs: str, mnt: Path, rows: list[dict]) -> None:
    from doc2md.security.perms import ensure_private, is_private
    d = mnt / "permcase"
    d.mkdir(exist_ok=True)
    f = d / "state.sqlite"
    f.write_bytes(b"x")
    warn_dir = ensure_private(d, 0o700)
    warn_file = ensure_private(f, 0o600)
    mode_dir = d.stat().st_mode & 0o777
    mode_file = f.stat().st_mode & 0o777
    priv_dir, priv_file = is_private(d), is_private(f)
    # 判据口径（CH-499 修正）：**以产品自己的告警面为准**，不再用「other 位是否残留」
    # 这条旧启发式。旧口径在合成权限卷上是恒真的漏报源——回读恰好 0700、other 全零，
    # 于是 `ensure_private` 静默、本探针也跟着报 enforced。现在 `warn_file` 非空即
    # 说明产品如实报告了「权限位在本卷不可表达」。
    decorative = bool(warn_file) or bool(warn_dir)
    rows.append(say(fs, "perm",
                    "decorative" if decorative else "enforced",
                    dir_mode=oct(mode_dir), file_mode=oct(mode_file),
                    is_private_dir=priv_dir, is_private_file=priv_file,
                    warnings=warn_dir + warn_file))
    # 跨用户可读性的最坏面：0600 未生效时，同机另一用户能直接读正文
    rows.append(say(fs, "perm_readable_by_group_other",
                    "yes" if decorative else "no",
                    mode=oct(mode_file)))
    # 「合成权限」取证（本轮量出来的决定性一条）：FAT32/exFAT 上 chmod 是 no-op，
    # 但 stat 回读恰好是 0700 → `ensure_private` 的「other 位仍在」启发式**不触发**，
    # 隐私告警被静默吞掉。判据：把两个文件分别 chmod 0600 / 0777，回读是否不同。
    probe = d / "chmodprobe"
    seen: dict[str, str] = {}
    for name, want in (("0600", 0o600), ("0777", 0o777)):
        probe.unlink(missing_ok=True)
        probe.write_bytes(b"y")
        os.chmod(probe, want)
        seen[name] = oct(probe.stat().st_mode & 0o777)
    synthetic = seen["0600"] == seen["0777"] and seen["0777"] != "0o777"
    rows.append(say(fs, "perm_chmod_effective",
                    "synthetic" if synthetic else "real",
                    requested_vs_observed=seen,
                    mount_opts=_mount_opts(mnt),
                    note=("chmod 无效：回读值恒为挂载默认，ensure_private 的旧启发式"
                          "只看 other 位 → 漏报" if synthetic else
                          "chmod 真实生效：请求值与回读一致")))


def probe_symlink(fs: str, mnt: Path, rows: list[dict]) -> None:
    from doc2md.security.dirguard import WalkLimits, guard_walk, is_link_like
    outer = mnt / "outside"
    outer.mkdir(exist_ok=True)
    (outer / "secret.pdf").write_text("secret", encoding="utf-8")
    root = mnt / "walkroot"
    (root / "inner").mkdir(parents=True, exist_ok=True)
    (root / "inner" / "ok.pdf").write_text("body", encoding="utf-8")
    link = root / "escape"
    made = False
    err = ""
    try:
        os.symlink(str(outer), str(link))
        made = True
    except OSError as exc:
        err = f"{type(exc).__name__}:{exc.errno}"
    got_link_flag = is_link_like(link) if link.exists() else None
    nofollow = guard_walk(root, WalkLimits(follow_symlinks=False, max_depth=20))
    follow = guard_walk(root, WalkLimits(follow_symlinks=True, max_depth=20))
    escaped = [p for p in follow.files if "secret.pdf" in p]
    rows.append(say(fs, "symlink", "supported" if made else "unsupported",
                    errno=err, is_link_like=(got_link_flag if made else "n/a"),
                    nofollow_files=len(nofollow.files),
                    nofollow_skipped_links=nofollow.skipped_symlinks,
                    follow_files=len(follow.files),
                    follow_escaping_skipped=follow.skipped_escaping_symlinks,
                    escaped=bool(escaped), escaped_paths=escaped[:2],
                    note=("软链可用：不跟随必须跳过、跟随必须被 realpath containment 挡住"
                          if made else "该卷造不出软链 → containment 在此卷**不可证**（"
                                      "不得把「没撞上」写成「撞不上」）")))


def probe_case(fs: str, mnt: Path, rows: list[dict]) -> None:
    d = mnt / "case"
    d.mkdir(exist_ok=True)
    a = d / "Report.PDF"
    a.write_text("x", encoding="utf-8")
    collision = (d / "report.pdf").exists()
    (d / "report.pdf").unlink(missing_ok=True) if collision else None
    a.unlink(missing_ok=True)
    rows.append(say(fs, "case_sensitivity",
                    "insensitive" if collision else "sensitive",
                    note="不敏感卷上消毒同名/异大小写会互相覆盖产物"))


def probe_names(fs: str, mnt: Path, rows: list[dict]) -> None:
    from doc2md.security.names import assert_name_safe, sanitize_name
    raw = ["第 1 章 · 报告 🔐.pdf", "CON.txt", "aux..log",
           "a" * 200 + ".pdf", "系统 prompt/斜杠 反斜杠\\冒号:.pdf",
           "\U0001F600" * 40 + ".pdf"]
    problems: list[str] = []      # 产品侧异常（消毒/断言口径）
    fs_rejects: list[str] = []    # 文件系统侧拒写（属该卷语义，不是缺陷）
    written = 0
    for name in raw:
        try:
            safe = sanitize_name(name)
            assert_name_safe(safe)
        except Exception as exc:                       # noqa: BLE001 取证面：任何异常都要显形
            problems.append(f"{name[:24]!r}: {type(exc).__name__}:{exc}")
            continue
        p = mnt / f"nm_{safe}"
        try:
            p.write_text("ok", encoding="utf-8")
        except OSError as exc:
            fs_rejects.append(f"{safe[:24]!r}: {type(exc).__name__}:{exc.strerror}")
            continue
        try:
            mismatch = (p.read_text(encoding="utf-8") != "ok"
                        or not any(x.name == p.name for x in mnt.glob("nm_*")))
        except OSError as exc:
            problems.append(f"{safe[:24]!r}: readback {type(exc).__name__}:{exc.strerror}")
            continue
        if mismatch:
            problems.append(f"{safe[:24]!r}: readback/direntry mismatch")
        written += 1
        p.unlink(missing_ok=True)
    verdict = ("problem" if problems else ("fs_rejects" if fs_rejects else "clean"))
    rows.append(say(fs, "names", verdict, tried=len(raw), written=written,
                    problems=problems[:6], fs_rejects=fs_rejects[:6]))


def probe_store(fs: str, mnt: Path, rows: list[dict]) -> None:
    """建库 + WAL + 锁 + **一次真实队列转换**（外卷上的端到端腿）。

    CH-499 第一轮取证在这里踩了两个坑，都已改正并留字：
    ① 只用 `doc2md convert` 判 `docs` 计数是**错的**——单文件 convert 走的是「产物
       直写」路径，根本不碰状态库（本机实测：out/ 有 zip、state.sqlite 连表都没有）。
       索引行只有队列路径（`work --enqueue` + `work --once`）才写，所以本腿走 work。
    ② 给子进程准备了 `DOC2MD_HOME` 却忘了传 `env`，等于用宿主配置跑沙箱——
       与 CH-498 / B-27 登记的是同一个坑（`--root` 不参与配置发现）。
    """
    from doc2md.storage.db import Store
    root = mnt / "work"
    for sub in ("src", "out", "logs", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    src = GOLDEN / "small_3p.pdf"
    if not src.exists():
        rows.append(say(fs, "store", "skipped_no_corpus", corpus=str(src)))
        return
    shutil.copyfile(src, root / "src" / "small_3p.pdf")
    store = Store(root / "state.sqlite")
    jm = store.q("PRAGMA journal_mode")[0][0]
    store.close()
    env = dict(os.environ)
    env["DOC2MD_HOME"] = str(root)
    (root / "doc2md.toml").write_text(
        "[paths]\n" f'root = "{root}"\n' "[perf]\nworkers = 1\n", encoding="utf-8")
    # `--json` 只挂在 `convert` 子命令上（`doc2md/cli.py:179`），work 不接受：
    # 传了会 argparse 退 2，把探针自己变成「缺陷」。work 本身恒打一行 JSON 汇总。
    enq = _run([DOC2MD, "work", "--enqueue", str(root / "src" / "small_3p.pdf"),
                "--root", str(root)], timeout=600, env=env)
    r = _run([DOC2MD, "work", "--once", "--workers", "1", "--root", str(root)],
             timeout=900, env=env)
    rc = enq.returncode or r.returncode
    out = sorted((root / "out").glob("*.zip"))
    sidecars = sorted(p.name for p in (root / "out").glob("._*"))
    mode = (root / "state.sqlite").stat().st_mode & 0o777
    con = sqlite3.connect(str(root / "state.sqlite"))
    try:
        docs = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        blocks = con.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        status = con.execute("SELECT status FROM tasks").fetchall()
        lease_cols = [c[1] for c in con.execute("PRAGMA table_info(tasks)")]
    finally:
        con.close()
    blocked = "E4131" in (r.stderr or "") + (r.stdout or "") + (enq.stderr or "")
    verdict = ("blocked_by_min_free_mb" if blocked else
               ("ok" if rc == 0 and docs == 1 and blocks > 0 and out else "defect"))
    rows.append(say(fs, "store", verdict, journal_mode=jm, db_mode=oct(mode),
                    enqueue_rc=enq.returncode, work_rc=r.returncode,
                    docs=docs, blocks=blocks,
                    task_status=[s[0] for s in status],
                    lease_columns_present=("owner" in lease_cols
                                           and "lease_expires_at" in lease_cols),
                    zips=[p.name for p in out], appledouble_sidecars=sidecars,
                    stderr=((r.stderr or "") + (enq.stderr or "")).strip()[:200]))
    # 非 Apple 文件系统会另落 AppleDouble 边车（._*）：产物目录出现「一名两文件」，
    # 备份/校验/展示若按目录计数就会把边车算成产物。
    rows.append(say(fs, "artifact_sidecars",
                    "appledouble_present" if sidecars else "none",
                    count=len(sidecars), names=sidecars[:4]))


def run_fs(fs: str, vol: str, base: Path, keep: bool) -> tuple[list[dict], str | None]:
    v = Volume(base, fs, vol)
    fail = v.create()
    if fail:
        v.detach()
        return [say(fs, "mount", "unavailable", detail=fail)], fail
    rows: list[dict] = []
    print(json.dumps({"fs": fs, "mounted_at": str(v.mnt), "statfs_type": v.fs_type()},
                     ensure_ascii=True), flush=True)
    for fn in (probe_perm, probe_symlink, probe_case, probe_names, probe_store):
        try:
            fn(fs, v.mnt, rows)
        except Exception as exc:                       # noqa: BLE001 取证台不得因单项崩整个卷
            rows.append(say(fs, fn.__name__, "raised",
                            exc=f"{type(exc).__name__}:{exc}",
                            tb=traceback.format_exc(limit=2)))
    if not keep:
        v.detach()
    return rows, None


def main(argv=None) -> int:
    _console_safe()
    ap = argparse.ArgumentParser(
        description="CH-499 / B-14 跨文件系统语义取证台（macOS hdiutil 造卷）")
    ap.add_argument("--fs", default="all",
                    help="只跑某一种文件系统名（默认 all：FAT32 / exFAT / 大小写敏感 APFS）")
    ap.add_argument("--base", default="/tmp/b14_crossfs")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    if sys.platform != "darwin":
        print(json.dumps({"error": "needs_macos_hdiutil", "platform": sys.platform},
                         ensure_ascii=True))
        return 2
    base = Path(args.base)
    shutil.rmtree(base, ignore_errors=True)
    (base / "mnt").mkdir(parents=True, exist_ok=True)
    wanted = [t for t in FS_LIST if args.fs == "all" or t[0] == args.fs]
    if not wanted:
        print(json.dumps({"error": "unknown_fs", "want": args.fs}, ensure_ascii=True))
        return 2
    all_rows: list[dict] = []
    mount_failures: list[str] = []
    for fs, vol in wanted:
        rows, fail = run_fs(fs, vol, base, args.keep)
        all_rows.extend(rows)
        if fail:
            mount_failures.append(f"{fs}: {fail}")
    defects = [r for r in all_rows if r.get("verdict") in ("defect", "raised", "problem")]
    if not args.keep:
        # 取证跑完不留残卷：映像由各 `Volume.detach()` 删除，这里收掉挂载点空目录，
        # 免得 `--keep` 之外的每次复跑都在 /tmp 堆一层沙箱（Step「无残留」的自查口径）。
        shutil.rmtree(base, ignore_errors=True)
    print(json.dumps({"fs_attempted": [f for f, _ in wanted],
                      "mount_failures": mount_failures,
                      "rows": len(all_rows), "defects": len(defects),
                      "verdict": ("defect" if defects else
                                  ("no_volume" if len(mount_failures) == len(wanted)
                                   else "ok"))},
                     ensure_ascii=True, sort_keys=True), flush=True)
    if defects:
        return 3
    return 2 if len(mount_failures) == len(wanted) else 0


if __name__ == "__main__":
    sys.exit(main())
