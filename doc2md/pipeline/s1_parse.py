"""S1 解析层 —— 文本型 PDF / DOCX 抽取为页级行结构。

范围纪律（上游 v2.4 Delta §10-1，方案 1.2 非目标）：不做图片 / OCR / 扫描件 / 公式；本层只处理可抽取文本层。

CH-459（FR-104 / FR-107 收口）：本层补三组结构化信号，供 S3 产出完整七类块——
- `Line.mono`：等宽字体行（代码块信号；取自 fontname，阈值见 `_is_mono`）；
- `Line.row`：表格行的单元格文本（PDF 按行内字符横向间隙聚类，DOCX 取 `w:tbl` 真实单元格）；
- `Line.link` / `link_internal` / `link_dest`：链接（PDF 取 Link 标注的 URI / GoTo，
  DOCX 取 `w:hyperlink` 的关系目标或书签锚点）。
另补 `Line.level_hint`：DOCX 段落样式名显式声明的标题层级（"Heading N"），
S3 据此判定标题——原实现只看字号/粗体/编号，DOCX 的 Heading 样式全部落成段落。

CH-476（R7-GAP-1，决策 D4「提取而非拒收」）：`Line.has_revision` + 修订痕迹的
「接受后」口径——DOCX 段落 / 表格行含 `w:ins`（插入）或 `w:del`（删除）时，正文按
「全部接受修订」的样子提取（插入并入、删除不入），并给该行打旗标。原实现只走
`p.text` / `p.runs`，而 python-docx 不读 `w:ins` 内的 run，修订插入的正文整段静默丢失。

CH-476（R7-GAP-2/3，决策 D7）：`read_docx` 补两处内容保真——
- 脚注 / 尾注（`word/footnotes.xml`、`word/endnotes.xml`）正文抽为尾部段落行，
  `Line.note_kind` 标来源；原实现只遍历 document.xml 的 body，注文整块丢失；
- 表格单元格内多段落以单空格相连（原 `"".join(tc.iter(w:t))` 直接粘连 = 吃掉段落边界）。

CH-476（R7-PERF-d/g）：DOCX 解析两处热点（口径不变，产物逐字节等价）——
- 段落样式按 `w:pStyle/@w:val` 记忆化（`_DocxCtx.styles`）：python-docx 的
  `paragraph.style` 每次访问都重扫整张 styles part，实测 185µs/段、10k 段文档
  仅样式解析即 3.7s（占 `read_docx` 九成）；样式只有几十种，记忆化后同样式段落直取；
- `w:hyperlink` 改为文档级预检（`_DocxCtx.has_links`）：无链接的文档不再逐段跑
  `.//w:hyperlink`（实测 1.6µs/段，10 万段 ≈ 160ms）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

MAGIC_PDF = b"%PDF-"
MAGIC_ZIP = b"PK\x03\x04"
DOCX_REQUIRED_PART = b"word/document.xml"

# CH-459：等宽字体的识别口径（PDF fontname / DOCX run.font.name，均转小写后子串匹配）。
# 只认明确的等宽家族，不把「无衬线」当代码——误判面比漏判大得多。
_MONO_HINTS = ("mono", "courier", "consolas", "menlo", "monaco")


@dataclass
class Line:
    text: str
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    strike: bool = False
    link: str | None = None
    link_internal: bool = False
    link_dest: str = ""
    mono: bool = False
    row: list[str] | None = None            # CH-459：表格行单元格（非 None 即行候选）
    row_explicit: bool = False              # CH-459：行来自真实表格对象（DOCX w:tbl），
                                            # 而非 PDF 几何推断——S3 的防误判守卫只对后者生效
    level_hint: int | None = None           # CH-459：DOCX Heading 样式显式层级
    has_revision: bool = False              # CH-476（R7-GAP-1）：DOCX 段落/表格行含
                                            # `w:ins` / `w:del` 修订痕迹（正文已按
                                            # 「接受后」口径提取，此信号供块级留痕）
    note_kind: str = ""                     # CH-476（R7-GAP-2）：该行来自 DOCX 注
                                            # （"footnote" / "endnote"），空串=正文
    x0: float = 0.0
    char_w: float = 0.0                     # CH-482：等宽字符实测宽度（pt，非等宽行 0）
                                            # ——x0 含行首空白，字宽不能由 (x1-x0)/len(text)
                                            # 反推；S3 的缩进量化用它把 x0 差换算成空格级数


@dataclass
class RawPage:
    index: int                      # 0-based
    lines: list[Line] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(ln.text.strip()) for ln in self.lines)


def sniff(path: str | Path) -> str:
    """magic 判定：非文本型 PDF / DOCX 必须是契约内的 415 / E4151。

    CH-451：原实现抛裸 `ValueError`，CLI 面逃到 `main()` 之外（栈回溯 + 退出码非契约值），
    API 面由 Flask 兜成 500 —— 与 CH-434（消毒失败裸 ValueError → 500）同一类错误：
    「输入安全」判定在两个入口都不可判定。
    """
    from ..errors import E4151_UnsupportedType
    p = Path(path)
    try:
        # CH-469：`with` 关闭句柄（原 `p.open("rb").read(8)` 依赖 GC 回收）
        with p.open("rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        raise E4151_UnsupportedType(f"cannot read {p.name}: {exc}", detail={"file": p.name})
    if head.startswith(MAGIC_PDF):
        return "pdf"
    if head.startswith(MAGIC_ZIP):
        try:
            import zipfile
            with zipfile.ZipFile(p) as z:
                names = set(z.namelist())
            if DOCX_REQUIRED_PART.decode() in names:
                return "docx"
        except Exception:
            pass
    raise E4151_UnsupportedType(f"unsupported magic for {p.name}",
                                detail={"file": p.name, "magic": head.hex()})


def _open_source(open_fn, path: str | Path, kind: str):
    """源文档打开边界：打不开 → 415/E4151（方案 附录 D：magic 判定不支持的类型
    （含不可读）；打开源文件 `OSError` 同归此码）。

    CH-457（T6 大语料实测暴露）：魔术码合法但结构损坏的 PDF（截断 / 无 xref）在
    pdfplumber open 阶段抛 `PdfminerException`，原实现未映射 → 冒泡成 E5000 内部
    错误，「输入损坏」被误报成「工具故障」（CLI 退出码 5 而非 4，API 500 而非 415，
    任务终态 failed 且错误码不可判定）。只映射打开失败：页级抽取异常不在此冒充
    「不可读」，仍走既有失败路径（那是另一类失败，精度优先）。
    detail 只含文件名与异常类名（NFR-20：异常消息可能夹带文档内容，不带入）。
    """
    from ..errors import Doc2mdError, E4151_UnsupportedType
    try:
        return open_fn()
    except Doc2mdError:
        raise
    except Exception as exc:  # noqa: BLE001 — 打开边界统一收敛为「输入不可读」
        raise E4151_UnsupportedType(
            f"unreadable {kind}: {Path(path).name}",
            detail={"file": Path(path).name, "exc": type(exc).__name__},
        ) from exc


def read_pdf(path: str | Path, page_span: tuple[int, int] | None = None,
             chunk_pages: int | None = None, fmt: str | None = None) -> list[RawPage]:
    """页级行结构 + 字号 / 粗斜体（pdfplumber chars）。

    page_span=(start, end) 半开区间，供 S2 分片按页范围粗切使用。
    chunk_pages：每重新打开一次文档最多解析多少页（CH-447，低配机内存纪律）。
    fmt：调用方已嗅探的格式（CH-459 消除 convert 内二次 sniff 开文件；缺省仍自查）。
    """
    import pdfplumber
    out: list[RawPage] = []
    # CH-469：整读且不分块时不再为「数页数」单独 open 一次——那次 open 与首个
    # 分块的 open 重复解析 xref/trailer（320 页 / chunk=50 时共 8 次打开）。
    # 分块模式仍需预先知道页数来算片边界，保持原口径。
    lazy_total = page_span is None and not (chunk_pages and chunk_pages > 0)
    total = 0 if lazy_total else _pdf_page_count(path)
    if page_span:
        start, end = max(0, page_span[0]), min(total, page_span[1])
    else:
        start, end = 0, total
    if not lazy_total and end <= start:
        return out
    step = chunk_pages if chunk_pages and chunk_pages > 0 else (end - start)
    lo_range = range(start, end, step) if not lazy_total else range(0, 1)

    for lo in lo_range:
        hi = min(lo + step, end)
        # 每个分块独立 open/close：pdfminer 的 LRUCachingObjectCache / 页对象
        # 随 PDF 实例一起释放，驻留峰值从「整本文档」降为「一个分块」。
        with _open_source(lambda: pdfplumber.open(str(path)), path, "pdf") as pdf:
            if lazy_total:              # 单片路径：页数在同一个 handle 上读出
                total = len(pdf.pages)
                lo, hi = 0, total
            for i in range(lo, hi):
                page = pdf.pages[i]
                chars = page.chars or []
                cs_index = _char_index(chars)
                lines: list[Line] = []
                geoms: list[tuple[float, float]] = []
                for ln in (page.extract_text_lines() or []):
                    top = float(ln.get("top", 0.0) or 0.0)
                    bottom = float(ln.get("bottom", 0.0) or 0.0)
                    lchars = _slice_chars(chars, cs_index, top, bottom)
                    st = _line_style(lchars)
                    lines.append(Line(text=ln.get("text", ""), size=st[0],
                                      bold=st[1], italic=st[2], strike=st[3],
                                      mono=st[4],
                                      row=_row_cells(lchars),
                                      x0=float(ln.get("x0", 0.0) or 0.0),
                                      char_w=_char_width(lchars) if st[4] else 0.0))
                    geoms.append((top, bottom))
                _attach_links(lines, geoms, page)
                out.append(RawPage(index=i, lines=lines))
                page.flush_cache()
    return out


def _attach_links(lines: list[Line], geoms: list[tuple[float, float]], page) -> None:
    """CH-459（FR-107/108）：Link 标注按几何覆盖附着到行。

    只认两类动作：URI（外部，`link`）与 GoTo（内部，`link_internal` + `link_dest`）；
    其余动作（Launch / JavaScript 等）不抽——执行类链接不是本管线的消费对象。
    一个标注只附着到第一根相交的行（审阅面每块只消费一个 URL，多链接块按保守口径）。
    """
    for a in (getattr(page, "annots", None) or []):
        if not isinstance(a, dict):
            continue
        atop, abot = a.get("top"), a.get("bottom")
        if atop is None or abot is None:
            continue
        uri = a.get("uri")
        dest = ""
        if uri:
            uri = str(uri)
        else:
            action = (a.get("data") or {}).get("A") or {}
            if not str(action.get("S", "")).endswith("GoTo"):
                continue                      # 只认 URI / GoTo，其余动作不抽
            d = action.get("D")
            if isinstance(d, bytes):
                dest = d.decode("utf-8", "replace")
            elif isinstance(d, (list, tuple)) and d:
                # 显式目标数组 [page, /XYZ, x, y, z]（与命名目标同样常见）：
                # 取页号作 dest；原 str(list) 会产出 "['/XYZ', ...]" 垃圾串
                try:
                    dest = str(int(d[0]))
                except (TypeError, ValueError):
                    dest = ""
            else:
                dest = str(d or "")
        for ln, (top, bottom) in zip(lines, geoms):
            if ln.link or ln.link_internal:
                continue
            # 行带与标注带在纵向相交（标注通常只覆盖链接文字那一行的条带）
            if float(atop) < bottom and float(abot) > top:
                if uri:
                    ln.link = uri
                else:
                    ln.link_internal = True
                    ln.link_dest = dest
                break


def _pdf_page_count(path: str | Path) -> int:
    import pdfplumber
    with _open_source(lambda: pdfplumber.open(str(path)), path, "pdf") as pdf:
        return len(pdf.pages)


def _char_index(chars: list[dict]) -> tuple[list[int], list[float]]:
    """预排序索引：(原始下标序, 对应的 top 升序序列)。

    _line_style 原本对每行做一次 O(全页 chars) 线性过滤，实测是 S1 的 CPU 热点
    （15.6k 行 × 每行重扫 → 单文件 320 页 profile 里占 27/100s）。
    这里用 bisect 取区间，但**取回的下标会再排序还原成原始 char 顺序**，
    使众数平票时的 `Counter.most_common` 平局判定与逐行全扫完全一致
    （实测 120 页 / 5613 行 0 处不符）。
    """
    tops = [float(c.get("top", 0.0)) for c in chars]
    order = sorted(range(len(chars)), key=lambda j: (tops[j],
                                                     float(chars[j].get("x0", 0.0))))
    return order, [tops[j] for j in order]


def _slice_chars(chars: list[dict], index: tuple[list[int], list[float]],
                 top: float, bottom: float) -> list[dict]:
    from bisect import bisect_left
    order, sorted_tops = index
    lo = bisect_left(sorted_tops, top - 1.0)
    hi = bisect_left(sorted_tops, max(bottom, top + 1.0))
    if lo >= hi:
        return []
    return [chars[j] for j in sorted(order[lo:hi])]


def _line_style(chars: list[dict]) -> tuple[float, bool, bool, bool, bool]:
    """取**已按几何区间切好**的行内 char 的众数字号与样式多数派。

    CH-459：第五返回值 `mono` = 行内多数字体为等宽家族（代码块信号）。
    CH-471（PIPE-2）：入参 `chars` 已由 `_slice_chars` 用 `[top-1.0, max(bottom,
    top+1.0))` 同一边界切好，原此处再过滤一遍**恒等于原列表**（逐行全字符比对，
    与 `_char_index` 的 bisect 优化自相矛盾），删除之——G6 逐字节等价。
    """
    from collections import Counter
    cs = chars
    if not cs:
        return (0.0, False, False, False, False)
    size = Counter(round(float(c.get("size", 0.0)), 1) for c in cs).most_common(1)[0][0]
    n = len(cs)
    bold = sum(1 for c in cs if _is_bold(c)) / n >= 0.6
    italic = sum(1 for c in cs if _is_italic(c)) / n >= 0.6
    strike = sum(1 for c in cs if c.get("strike")) / n >= 0.6
    mono = sum(1 for c in cs if _is_mono(c)) / n >= 0.6
    return (size, bold, italic, bool(strike), mono)


def _is_mono(c: dict) -> bool:
    return any(h in str(c.get("fontname", "")).lower() for h in _MONO_HINTS)


def _char_width(chars: list[dict]) -> float:
    """CH-482：行内字符宽度中位数（pt）。

    `Line.x0` 是从**行首空白**起算的，`(x1-x0)/len(text)` 会把缩进空格摊进字宽
    （空白已被 `extract_text_lines` 从 text 剥掉，但仍在 chars/几何里）。逐字符取宽
    再取中位数不受行首空白影响；等宽字体下空格与可见字符同宽，故不区分空白。
    """
    widths = []
    for c in chars:
        if not (c.get("text") or ""):
            continue
        w = float(c.get("x1", 0.0) or 0.0) - float(c.get("x0", 0.0) or 0.0)
        if w > 0:
            widths.append(w)
    if not widths:
        return 0.0
    widths.sort()
    return widths[len(widths) // 2]


def _row_cells(chars: list[dict]) -> list[str] | None:
    """CH-459（FR-104 表格）：行内字符按横向间隙聚类成单元格。

    间隙阈值 = max(8pt, 1.6 × 行内字符宽度中位数)——单空格（约 1/3 字宽）不会误切，
    真正的列间空档（≥ 1.6 字宽）才分列。返回 None 表示「不是多列行」（单列/无间隙），
    由 S3 结合连续性判定是否成表（单行多列可能是排版性空距，不是表格）。
    """
    cs = [c for c in chars if (c.get("text") or "").strip()]
    if len(cs) < 2:
        return None
    # CH-464：聚类假设 x 升序，但内容流不保证（双栏回写、生成器先画右列）——
    # 乱序时整行被切碎或判成单列。按 x0 排序后再聚类。
    cs.sort(key=lambda c: float(c.get("x0", 0) or 0))
    widths = sorted(float(c.get("x1", 0) or 0) - float(c.get("x0", 0) or 0) for c in cs)
    median_w = widths[len(widths) // 2] or 1.0
    gap_th = max(8.0, 1.6 * median_w)
    cells: list[list[str]] = [[cs[0].get("text", "")]]
    prev_x1 = float(cs[0].get("x1", 0) or 0)
    for c in cs[1:]:
        x0 = float(c.get("x0", 0) or 0)
        if x0 - prev_x1 > gap_th:
            cells.append([])
        cells[-1].append(c.get("text", ""))
        prev_x1 = max(prev_x1, float(c.get("x1", 0) or 0))
    if len(cells) < 2:
        return None
    return ["".join(seg).strip() for seg in cells]


def _is_bold(c: dict) -> bool:
    if c.get("bold"):
        return True
    fontname = str(c.get("fontname", "")).lower()
    return "bold" in fontname or "black" in fontname or c.get("weight", 0) >= 600


def _is_italic(c: dict) -> bool:
    if c.get("italic"):
        return True
    return "italic" in str(c.get("fontname", "")).lower() or "oblique" in str(
        c.get("fontname", "")).lower()


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass
class _DocxCtx:
    """一次 `read_docx` 的文档级预检 / 记忆化状态（CH-476 / R7-PERF-d、g）。

    `styles`：`w:pStyle/@w:val` → 样式名。python-docx 的 `paragraph.style` 每次访问
    都调 `Styles.get_by_id` → 未命中显式样式时 `default_for()` 重扫整张 styles part
    （对每个 `w:style` 做 enum 解析），实测 185µs/段。样式种类只有几十，按 style_id
    记忆化即把 O(段 × 样式) 降为 O(样式)。缓存的是**样式名字符串**（本层只用
    `style.name`），不缓存 python-docx 样式对象，避免跨段共享可变代理。
    `has_links`：该容器子树内是否存在 `w:hyperlink`。无则整篇跳过逐段 XPath——
    预检覆盖 body 全集，跳过条件与「该段 findall 返回空」严格等价。
    """
    styles: dict = field(default_factory=dict)
    has_links: bool = True


def _docx_ctx(root) -> _DocxCtx:
    """按遍历容器建预检上下文（body 与各 notes part 各一份，样式缓存由调用方共享）。"""
    return _DocxCtx(has_links=root.find(f".//{_W_NS}hyperlink") is not None)


def _docx_style_name(el, d, ctx: _DocxCtx) -> str:
    """段落样式名（记忆化）。与 `p.style.name` 同口径：无显式样式 / 样式不存在时
    python-docx 回落到默认段落样式，本函数走同一 `Styles.get_by_id`，只是每个
    style_id 只解析一次；样式缺失（返回 None）时同样得到空串。
    """
    sid = el.style                                  # CT_P.style：读 w:pStyle/@w:val，无全表扫
    if sid not in ctx.styles:
        from docx.enum.style import WD_STYLE_TYPE
        st = d.styles.get_by_id(sid, WD_STYLE_TYPE.PARAGRAPH)
        ctx.styles[sid] = (st.name or "") if st is not None else ""
    return ctx.styles[sid]



def _docx_para_line(p, d, rels=None, ctx: _DocxCtx | None = None) -> Line | None:
    """段落 → Line（含 CH-459 的等宽 / 超链接 / 标题层级提示 / 分页符信号）。

    CH-476（R7-GAP-1，决策 D4「提取而非拒收」）：含修订痕迹的段落改走「接受后」口径。
    python-docx 的 `p.text` / `p.runs` 只认 `w:p` 直接子级里的 `w:r`，被 `w:ins`
    包裹的插入 run 不在其中（实测 `p.text` 里 "inserted" 消失）——修订插入的正文
    会整段静默丢失。无修订的段落走原路径，产物字节不变（G6）。

    `rels`（CH-476 / R7-GAP-2）：超链接关系集合，默认取 document part 的 rels；
    脚注 / 尾注 part 调用时必须传自己的 rels——rId 只在所属 part 的关系表内有意义，
    拿错关系表会把链接指到别的 target（制造错误数据）。
    `ctx`（CH-476 / R7-PERF-d、g）：文档级预检与样式记忆化；缺省新建（等价于
    「不跳过任何段、不共享缓存」，即原行为），供独立调用与测试使用。
    """
    el = p._element
    if ctx is None:
        ctx = _DocxCtx()
    rev = _docx_has_revision(el)
    if rev:
        runs = _docx_all_runs(p)
        # `w:del` 里的文本是 `w:delText`，`Run.text` 本就不取 → 拼出来即「接受后」正文
        raw_text = "".join(r.text or "" for r in runs)
    else:
        runs = p.runs
        raw_text = p.text or ""
    if not raw_text.strip():
        return None
    size = 0.0
    bold = italic = strike = mono = False
    for r in runs:
        if r.font.size is not None:
            size = float(r.font.size.pt)
        bold = bold or bool(r.bold)
        italic = italic or bool(r.italic)
        strike = strike or bool(r.font.strike)
        mono = mono or _name_is_mono(r.font.name)
    # CH-482：等宽段的行首空白（空格 / `w:tab`）是代码内容，DOCX 没有 x0 几何可重建，
    # 丢了就无从恢复——只对等宽行保留前导空白，散文行仍走 strip（原口径不变）。
    text = raw_text.rstrip() if mono else raw_text.strip()
    style = _docx_style_name(el, d, ctx)
    level = None
    if style.lower().startswith("heading"):
        try:
            level = max(1, min(6, int(style.split()[-1])))
        except Exception:
            level = 1
    link, link_internal, link_dest = _docx_para_link(p, d, text, rels, ctx.has_links)
    return Line(text=text, size=size or _style_size(style),
                bold=bold or style.lower().startswith("heading"),
                italic=italic, strike=strike, mono=mono,
                link=link, link_internal=link_internal, link_dest=link_dest,
                level_hint=level, has_revision=rev)


def _docx_has_revision(el) -> bool:
    """子树内是否有 `w:ins` / `w:del`（单次 `iter` 短路，不做两遍 xpath）。"""
    marks = (_W_NS + "ins", _W_NS + "del")
    return any(e.tag in marks for e in el.iter())


def _docx_all_runs(p) -> list:
    """段落内**全部** run（含 `w:ins` / `w:hyperlink` 包裹者），包装成 python-docx Run。

    只在 `_docx_has_revision` 命中时调用；`Run.text` 沿用 python-docx 口径
    （`w:tab`→\\t、`w:br`/`w:cr`→\\n、`w:delText` 不取），故与 `p.text` 同形。
    """
    from docx.text.run import Run
    return [Run(r, p) for r in p._element.iter(f"{_W_NS}r")]


def _name_is_mono(name: str | None) -> bool:
    return bool(name) and any(h in name.lower() for h in _MONO_HINTS)


def _docx_para_link(p, d, para_text: str, rels=None,
                    has_links: bool = True) -> tuple[str | None, bool, str]:
    """CH-459（FR-107/108）：段落内 `w:hyperlink` → (外部 URL, 是否内部, 锚点)。

    保守口径：只有超链接文字**完整覆盖**段落文本时才附着——行内局部链接
    （链接文字只是段落的一部分）本轮不抽：块级 links 模型无法表达「哪一段文字」
    是链接，硬附会把整块文本包成 `[整块](url)`，属于制造错误数据。

    `rels`：关系表；缺省用 `d.part.rels`（正文段落）。脚注 / 尾注传各自 part 的
    rels（CH-476 / R7-GAP-2），否则 rId 会撞进正文关系表指向错的 target。
    `has_links`（CH-476 / R7-PERF-d）：调用方已确认整个容器子树无 `w:hyperlink`
    时直传 False——该段 findall 必为空，跳过与否逐字节等价，省掉每段一次 XPath。
    """
    if not has_links:
        return None, False, ""
    links = p._element.findall(f".//{_W_NS}hyperlink")
    if not links:
        return None, False, ""
    if rels is None:
        rels = d.part.rels
    for h in links:
        texts = "".join(t.text or "" for t in h.iter(f"{_W_NS}t")).strip()
        if not texts or texts != para_text:
            continue
        rid = h.get(f"{_R_NS}id")
        if rid:
            try:
                rel = rels[rid]
                if rel.is_external:
                    return rel.target_ref, False, ""
            except KeyError:
                pass
        anchor = h.get(f"{_W_NS}anchor")
        if anchor:
            return None, True, anchor
    return None, False, ""


_NOTE_TAGS = {"footnote": f"{_W_NS}footnote", "endnote": f"{_W_NS}endnote"}
# Word 在 footnotes.xml / endnotes.xml 里放的分隔符占位注（页面底部的横线本体），
# 带 w:type="separator" / "continuationSeparator"——不是注文，抽进来即制造噪声行
_NOTE_SEPARATORS = ("separator", "continuationSeparator")


def _docx_notes_lines(d, ctx: _DocxCtx) -> list[Line]:
    """CH-476（R7-GAP-2，决策 D7）：脚注 / 尾注正文补抽为文档末尾的段落行。

    原实现只遍历 document.xml 的 body，`word/footnotes.xml` / `word/endnotes.xml`
    完全不读 → 学术、法律文档的注文整块静默丢失，而非目标清单从未排除它们。
    口径：
    - 一条注 = 一行；注内多段落以单空格相连（与表格单元格段落口径一致，R7-GAP-3）；
    - 分隔符占位注跳过；
    - 不伪造 `[^1]` 行内脚注标记：GFM 无脚注语法，正文处没有可指的锚点，硬造即死链
      （与 FR-107 内部链接一律纯文本的口径同理）。注文来源由 `Line.note_kind`
      带到块级 `flags.note_kind`，供审阅区分「尾部段落是注不是正文」。
    - 无注的文档（现有全部语料）返回空列表 → 产物字节不变（G6）。
    `ctx`：与正文共享样式记忆化；`has_links` 按注 part 自己的子树单独预检。
    """
    import docx.text.paragraph as _dp
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import parse_xml

    lines: list[Line] = []
    for reltype, kind in ((RT.FOOTNOTES, "footnote"), (RT.ENDNOTES, "endnote")):
        try:
            part = d.part.part_related_by(reltype)
        except KeyError:
            continue
        root = parse_xml(part.blob)
        local = _DocxCtx(styles=ctx.styles,
                         has_links=root.find(f".//{_W_NS}hyperlink") is not None)
        for note in root.findall(_NOTE_TAGS[kind]):
            if note.get(f"{_W_NS}type") in _NOTE_SEPARATORS:
                continue
            got = [_docx_para_line(_dp.Paragraph(el, d), d, part.rels, local)
                   for el in note.findall(f"{_W_NS}p")]
            got = [ln for ln in got if ln is not None]
            if not got:
                continue
            head, *rest = got
            text = head.text if not rest else " ".join(
                [head.text] + [ln.text for ln in rest])
            lines.append(replace(head, text=text, note_kind=kind))
    return lines


def _docx_cell_text(tc) -> str:
    """单元格正文：段落边界以单空格相连（CH-476 / R7-GAP-3，决策 D7）。

    原实现 `"".join(tc.iter(w:t))` 把单元格内多段落直接粘连（"段落1段落2"），
    等于吃掉段落边界。逐 `w:p` 取「接受后」文本再以单空格连接后，单段落单元格
    （现有全部语料）与原实现逐字节相同 → 产物不变（G6）。
    嵌套 `w:tbl` 的段落仍被拍平进本格：无版面模型，本轮不表达表格嵌套。
    """
    parts = ["".join(t.text or "" for t in p.iter(f"{_W_NS}t")).strip()
             for p in tc.iter(f"{_W_NS}p")]
    return " ".join(x for x in parts if x)


def _docx_table_rows(tbl) -> list[tuple[list[str], bool]]:
    """`w:tbl` → (单元格文本行, 该行是否含修订痕迹)；空单元格保留空串以对齐列数。

    CH-476（R7-GAP-1）：单元格取「接受后」视图（`w:ins` 内的 `w:t` 覆盖、`w:delText`
    不含），表格路径天然是接受口径，只需把修订信号带出来。
    CH-476（R7-GAP-3）：单元格内段落边界以单空格相连，见 `_docx_cell_text`。
    """
    rows: list[tuple[list[str], bool]] = []
    for tr in tbl.findall(f"{_W_NS}tr"):
        cells = [_docx_cell_text(tc) for tc in tr.findall(f"{_W_NS}tc")]
        if cells:
            rows.append((cells, _docx_has_revision(tr)))
    return rows


def read_docx(path: str | Path, fmt: str | None = None) -> list[RawPage]:
    """DOCX → 页级行结构（DOCX 无物理页，按节合成单页序列，每 40 行一页）。

    CH-459：按 body 顺序遍历（段落与表格交错，原实现只读 `d.paragraphs`，
    表格内容整体丢失——FR-104 的 table 块对 DOCX 从未有输入）；
    段落内 `<w:br w:type="page"/>` 合成为 `\\x0c` 行（S3 的 page_break 信号）。
    CH-476（R7-GAP-1）：修订痕迹按「接受后」口径提取——`w:ins` 插入并入正文、
    `w:del` 删除不入正文，命中的行打 `Line.has_revision` 供 S3 块级留痕。
    CH-476（R7-GAP-2，决策 D7）：正文行之后追加脚注 / 尾注行（`_docx_notes_lines`），
    注文不再整块丢失；无注的文档该行序为空，产物字节不变。
    fmt：调用方已嗅探的格式（形参保留与 read_pdf 对齐；DOCX 分支不使用）。
    """
    import docx
    import docx.text.paragraph as _dp

    d = _open_source(lambda: docx.Document(str(path)), path, "docx")
    ctx = _docx_ctx(d.element.body)
    lines: list[Line] = []
    for child in d.element.body:
        tag = child.tag
        if tag == f"{_W_NS}p":
            ln = _docx_para_line(_dp.Paragraph(child, d), d, ctx=ctx)
            if ln is not None:
                lines.append(ln)
            if child.findall(f".//{_W_NS}br[@{_W_NS}type='page']"):
                lines.append(Line(text="\x0c", size=0.0))
        elif tag == f"{_W_NS}tbl":
            for cells, rev in _docx_table_rows(child):
                lines.append(Line(text=" | ".join(cells), row=cells,
                                  row_explicit=True, has_revision=rev))
    lines += _docx_notes_lines(d, ctx)
    pages: list[RawPage] = []
    chunk = 40
    for i in range(0, max(1, (len(lines) + chunk - 1) // chunk)):
        pages.append(RawPage(index=i, lines=lines[i * chunk:(i + 1) * chunk]))
    return pages


def _style_size(style_name: str) -> float:
    s = style_name.lower()
    if s.startswith("title"):
        return 20.0
    if s.startswith("heading"):
        try:
            lvl = int(s.split()[-1])
        except Exception:
            lvl = 1
        return max(11.0, 18.0 - lvl * 1.5)
    return 10.5


def read_any(path: str | Path, page_span: tuple[int, int] | None = None,
             chunk_pages: int | None = None, fmt: str | None = None) -> list[RawPage]:
    fmt = fmt or sniff(path)
    if fmt == "pdf":
        return read_pdf(path, page_span, chunk_pages=chunk_pages, fmt=fmt)
    if page_span:
        return read_docx(path)[page_span[0]:page_span[1]]
    return read_docx(path)
