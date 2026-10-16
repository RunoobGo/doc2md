"""S1.5 体检门禁 —— 文本层质量采样判定。

CH-415 / P-63：采样由三点 [0, "mid", -1] 改为均匀 5 页 even5，
任一采样页不达标即拒收（E4221），报告附页级明细（哪一页、哪个指标）。

开发期发现并修正（CH-424，见方案 第十一章）：
even5 仍是**抽样**，坏页只要避开 5 个采样点就照样漏检（实测 badtail_late.pdf
60 页、坏区在第 51~53 页，even5 与三点口径同时漏过）。而 S1 已经把全部页的
文本抽出来了，逐页密度判定零额外解析成本。故新增 "auto" 模式：
  - 报告面仍是 even5 采样页（保持 CH-415 的页级明细可读性）；
  - 判定面覆盖全文档页密度，按「连续坏页成段」或「坏页占比超阈值」拒收。
  单页偶发稀疏（图表页、章末留白）不误杀 —— 这是全量判定必须配套的护栏，
  否则真实文档会大面积误拒。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..errors import Doc2mdError, E4221_GateReject
from .s1_parse import RawPage

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_NOISE = re.compile(r"^[\s\d\W_]+$")


def even5_indices(total_pages: int, k: int = 5) -> list[int]:
    """均匀 k 页索引；不足 k 页取全部页。"""
    if total_pages <= 0:
        return []
    if total_pages <= k:
        return list(range(total_pages))
    return sorted({round(i * (total_pages - 1) / (k - 1)) for i in range(k)})


def legacy3_indices(total_pages: int) -> list[int]:
    """旧三点口径（保留供回归对照，证明 even5 能拦下后段坏页）。"""
    if total_pages <= 0:
        return []
    if total_pages <= 3:
        return list(range(total_pages))
    return [0, total_pages // 2, total_pages - 1]


@dataclass
class PageReport:
    page: int
    chars: int
    cjk_ratio: float
    line_count: int
    noise_ratio: float
    ok: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    passed: bool
    mode: str
    sampled: list[int]
    pages: list[PageReport]
    reasons: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def detail(self) -> dict:
        return {
            "mode": self.mode,
            "sampled_pages": [p + 1 for p in self.sampled],
            "passed": self.passed,
            "page_detail": [
                {"page": p.page + 1, "chars": p.chars, "lines": p.line_count,
                 "cjk_ratio": round(p.cjk_ratio, 3), "noise_ratio": round(p.noise_ratio, 3),
                 "ok": p.ok, "reasons": p.reasons}
                for p in self.pages
            ],
            "stats": self.stats,
            "reasons": self.reasons,
        }


def _page_metrics(page: RawPage) -> tuple[int, float, int, float]:
    text = "\n".join(ln.text for ln in page.lines)
    chars = len(re.sub(r"\s", "", text))
    letters = len(_CJK.findall(text)) + len(_LATIN.findall(text))
    cjk_ratio = (len(_CJK.findall(text)) / letters) if letters else 0.0
    lines = [ln.text.strip() for ln in page.lines if ln.text.strip()]
    noise = sum(1 for ln in lines if _NOISE.match(ln))
    noise_ratio = (noise / len(lines)) if lines else 1.0
    return chars, cjk_ratio, len(lines), noise_ratio


def _page_chars(page: RawPage) -> int:
    """只要字符数（与 `_page_metrics` 的第 0 项同口径），跳过其余三项正则。

    CH-459（A-5）：门禁统计 `avg_chars` 原本对全文档再跑一遍完整 `_page_metrics`
    （4 个正则 × 全页数）。非采样页只需要字符数这一项，用本函数付 1 个正则的代价。
    """
    return len(re.sub(r"\s", "", "\n".join(ln.text for ln in page.lines)))


def _longest_run(flags: list[bool]) -> tuple[int, int]:
    best = cur = 0
    start = best_start = -1
    for i, f in enumerate(flags):
        if f:
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    return best, best_start


def _avg_chars(pages: list[RawPage]) -> float:
    if not pages:
        return 0.0
    return sum(_page_metrics(p)[0] for p in pages) / len(pages)


class GateCollector:
    """门禁采集器（CH-459 / A-5 + A-8）：采集与判定拆开的两段式实现。

    原 `check()` 要求一次拿到全文档页对象——与 convert「按片读取、单片驻留」
    （A-8，千页级 NFR-21）存在冲突：门禁一读全量，分片省下的内存又还回去。
    本类把**采集**与**判定**拆开，为流式喂入提供形状：
      - `feed(pages, offset=)` 逐片喂页，只保留判定必需的标量（每页 4 个数）
        与页级明细对象；页对象本身喂完即可释放；
      - `finish()` 是纯函数判定，不再触碰任何页对象——A-5 的「每页指标算两轮」
        由此消失（`avg_chars` 不再对全文档重跑 `_page_metrics`）。

    消费面如实标注（CH-473 / PIPE-12）：**当前生产路径只有 `check()` 一个入口，
    它一次性喂入全文档页列表（`feed(pages)`，offset=0）**；带非零 `offset` 的
    逐片流式喂入目前只有测试在演练（tests/test_batch2_storage.py）。convert 尚
    未改为按片喂页，因此「流式省内存」是**已具备未接线**的能力，不是已生效的
    行为——后续若把 convert 的读取循环接到 `feed(offset=)` 上，无需改本类。

    总页数必须在构造时给出：`even5` / `legacy3` 的采样点依赖总页数，而流式
    场景下采样点可能在对应页到达之前就要算出来。PDF 由 `page_count` 轻量取，
    DOCX 由整读后的页数给出。
    """

    def __init__(self, total_pages: int, min_chars: int = 120, mode: str = "auto",
                 bad_run_min: int = 2, bad_ratio_max: float = 0.05, empty_chars: int = 20):
        self.total = int(total_pages)
        self.min_chars = min_chars
        self.mode = mode
        self.bad_run_min = bad_run_min
        self.bad_ratio_max = bad_ratio_max
        self.empty_chars = empty_chars
        self.report_idx = even5_indices(self.total, 5)
        if mode == "legacy3":
            self.decision_idx = legacy3_indices(self.total)
        elif mode == "even5":
            self.decision_idx = self.report_idx
        elif mode == "auto":
            self.decision_idx = list(range(self.total))
        else:
            raise ValueError(f"unknown sample mode: {mode}")
        self._decision_set = set(self.decision_idx)
        self._need = self._decision_set | set(self.report_idx)
        self.metrics: dict[int, tuple] = {}
        self._chars_sum = 0
        self._seen = 0

    def feed(self, pages, offset: int = 0) -> None:
        """喂入一片页；`offset` 为该片首页在文档中的绝对索引。"""
        for k, page in enumerate(pages):
            i = offset + k
            if i in self._need:
                m = _page_metrics(page)
                self.metrics[i] = m
                self._chars_sum += m[0]
            else:
                self._chars_sum += _page_chars(page)
            self._seen += 1

    def _level(self, i: int, strict: bool) -> tuple[list[str], bool]:
        chars, _cjk, nlines, noise = self.metrics[i]
        r = []
        if chars < self.min_chars:
            r.append(f"chars={chars} < {self.min_chars}")
        if nlines == 0:
            r.append("no_text_lines")
        if noise > 0.9 and nlines > 0:
            r.append(f"noise_ratio={noise:.2f}")
        empty = chars < self.empty_chars or nlines == 0
        return r, (bool(r) if strict else empty)

    def finish(self, reject_on_any: bool = True) -> GateResult:
        """纯函数判定 + 拒收即抛；与旧 `check()` 逐字段同口径。"""
        # CH-469：构造声明的 total 大于实际喂入的页数时，原实现在
        # self.metrics[i] 处裸 KeyError（判定面与报告面两处都可能命中）。
        # 缺页属调用方契约违反，必须在判定前给出可判定的错误。
        missing = sorted(self._need - set(self.metrics))
        if missing:
            raise Doc2mdError(
                f"gate: {len(missing)} decision/report page(s) never fed "
                f"(total={self.total}, first missing page {missing[0] + 1})",
                detail={"missing_pages": [i + 1 for i in missing[:10]]})
        strict = self.mode != "auto"
        reasons: list[str] = []
        per_page = [(i,) + self._level(i, strict) for i in self.decision_idx]
        bad_flags = [hard for _, _, hard in per_page]
        n_bad = sum(bad_flags)

        if self.mode == "auto":
            run, run_start = _longest_run(bad_flags)
            ratio = n_bad / max(1, self.total)
            if run >= self.bad_run_min:
                reasons.append(f"consecutive_empty_pages={run} starting at page {run_start + 1}")
            if self.total >= 20 and ratio > self.bad_ratio_max:
                reasons.append(f"empty_page_ratio={ratio:.2%} > {self.bad_ratio_max:.0%}")
            if n_bad == self.total and self.total:
                reasons.append("all_pages_empty")
        else:
            if reject_on_any:
                for i, r, hard in per_page:
                    if hard:
                        reasons.append(f"page {i + 1}: {', '.join(r)}")
            else:
                n = len([1 for _, r, h in per_page if h])
                if n >= len(per_page):
                    reasons.append(f"all {n} sampled pages bad")

        pages_out: list[PageReport] = []
        for i in sorted(self._need):
            chars, cjk, nlines, noise = self.metrics[i]
            r, hard = self._level(i, strict)
            in_decision = i in self._decision_set
            pages_out.append(PageReport(page=i, chars=chars, cjk_ratio=cjk, line_count=nlines,
                                        noise_ratio=noise, ok=not (hard and in_decision),
                                        reasons=r))

        stats = {"total_pages": self.total, "decision_pages": len(self.decision_idx),
                 "empty_pages": n_bad,
                 "avg_chars": round(self._chars_sum / self._seen, 1) if self._seen else 0.0}
        res = GateResult(passed=not reasons, mode=self.mode, sampled=self.report_idx,
                         pages=pages_out, reasons=reasons, stats=stats)
        if not res.passed:
            raise E4221_GateReject("S1.5 gate rejected: bad text layer", detail=res.detail())
        return res


def check(pages: list[RawPage], min_chars: int = 120, mode: str = "auto",
          reject_on_any: bool = True, bad_run_min: int = 2,
          bad_ratio_max: float = 0.05, empty_chars: int = 20) -> GateResult:
    """S1.5 门禁判定（一次性入口，内部走 `GateCollector`）。

    坏页两级判据（开发期细化，避免误杀内容稀疏的正常文档）：
      - empty-ish：chars < empty_chars 或无文本行 → 真·坏页（模拟扫描插页）；
      - sparse   ：chars < min_chars → 记入页级明细供人工定位。
      "auto" 只对 empty-ish 成段/占比拒收（sparse 不单独触发）；
      "even5" / "legacy3" 保持方案与 v2.1 的严格原口径（任一判据不达标即拒收），
      作为对照基线 —— 实测两者对 badtail_late 漏检、对 links_pdf 误杀，即 CH-424 依据。

    mode:
      "auto"    —— CH-424 增强口径：报告面为 even5 采样页，判定面覆盖全文档页密度，
                   按「连续 empty-ish 段」或「empty-ish 占比超阈值」拒收；
      "even5"   —— 方案 CH-415 原口径（仅判 5 个采样页，任一不达标即拒收）；
      "legacy3" —— 旧三点口径，仅供回归对照。
    """
    c = GateCollector(len(pages), min_chars=min_chars, mode=mode,
                      bad_run_min=bad_run_min, bad_ratio_max=bad_ratio_max,
                      empty_chars=empty_chars)
    c.feed(pages)
    return c.finish(reject_on_any=reject_on_any)
