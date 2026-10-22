"""B-4 本机并发压测探针（optimization-report §6 B-4 / N-2 补充口径）。

200 份小 DOCX，workers=1（串行）vs 2 vs 4 首转计时 → 加速比；
并核对三档产物**逐字节等价**（CH-448 的「批量与串行产物一致」红线在本机
实测面）。复用 tests/n2_probe.py 的语料生成。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from n2_probe import BATCH, PY, ROOT, make_corpus, parse_results  # noqa: E402


def run_cli(args: list[str]) -> tuple[float, str]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [PY, "-c", "from doc2md.cli import main; main()", *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    dt = time.perf_counter() - t0
    if proc.returncode != 0:
        raise SystemExit(f"CLI failed rc={proc.returncode}\n{proc.stderr[-2000:]}")
    return dt, proc.stdout


def out_fingerprint(out: Path) -> dict[str, str]:
    """成员内容指纹（zip 容器带写入时间戳，跨次运行必不同——CH-448 的
    等价红线是成员字节口径，串行↔串行同样容器不等）。"""
    fp: dict[str, str] = {}
    for p in sorted(out.glob("*.zip")):
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                if not info.is_dir():
                    fp[f"{p.name}:{info.filename}"] = hashlib.sha256(
                        z.read(info.filename)).hexdigest()
    return fp


def main() -> None:
    make_corpus(200)
    src = BATCH / "src"
    results: dict[str, object] = {}
    fps: dict[str, dict[str, str]] = {}
    for workers in (1, 2, 4):
        out = BATCH / f"out_w{workers}"
        if out.exists():
            shutil.rmtree(out)
        dt, js = run_cli(["convert", str(src), "--out", str(out),
                          "--json", "--workers", str(workers)])
        rows = parse_results(js)
        ok = sum(1 for r in rows if r.get("status") == "ok")
        results[f"w{workers}_first_ms"] = round(dt * 1000)
        results[f"w{workers}_ok"] = ok
        fps[f"w{workers}"] = out_fingerprint(out)
        assert ok == 200, f"workers={workers}: ok={ok} != 200"
    # 产物逐字节等价（串行 vs 并发）
    assert fps["w1"] == fps["w2"] == fps["w4"], "并发产物与串行不等价！"
    results["artifact_equal_serial_vs_concurrent"] = True
    results["artifacts"] = len(fps["w1"])
    base = results["w1_first_ms"]
    for w in (2, 4):
        results[f"speedup_w{w}"] = round(base / results[f"w{w}_first_ms"], 2)

    # skip-existing 并发口径（workers=4 二转）
    out4 = BATCH / "out_w4"
    dt, js = run_cli(["convert", str(src), "--out", str(out4),
                      "--json", "--workers", "4", "--skip-existing"])
    skipped = sum(1 for r in parse_results(js) if r.get("status") == "skipped")
    results["w4_skip_ms"] = round(dt * 1000)
    results["w4_skipped"] = skipped
    assert skipped == 200, f"并发 skip: {skipped} != 200"
    results["w4_skip_ratio"] = round(
        results["w4_skip_ms"] / results["w4_first_ms"], 3)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
