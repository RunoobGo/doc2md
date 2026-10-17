"""CH-478 批次②（R6-PIPE-2 的 B-2 出口）：等价性门的缩进敏感度，用真实产物对照。

登记判据（12.7 的 R6-PIPE-2 行）写的是「构造缩进/围栏漂移样本，验证收紧后的
`char_similarity` 门能捕获（旧口径会漏过）」。本文件拿黄金样本 `codeindent_1p.pdf`
的**真实产物**做四类漂移对照，实测出三条结论：

1. **行首缩进漂移**：CH-475 收紧前的口径（`_WS.sub("", ln)` 删尽全部空白）对这类漂移
   是**结构失明**——归一化后两份产物逐行相同，相似度精确等于 1.0，无论多少处缩进都
   不会有一丁点下降；收紧后同一对在本语料上得 0.973958，判红（门 = 0.99）。
2. **内容级漂移**（围栏缺失 0.992327、`-`↔`*` 标记替换 0.994924）：收紧**不带来任何额外敏感度**，两种口径
   差值 < 0.001，且在本语料上**同判绿**。原登记把「列表标记替换」「围栏破损」也写成
   「旧口径失明、收紧后可见」是过度声明，这里钉成断言，防止后续再按那个口径立论。
3. **0.99 是粗大漂移探测器，不是等值证明**：判红与否取决于漂移量相对语料的分母——四类
   漂移里本语料只有缩进一类过阈，逐字节等值仍由 12.2 的 sha 对照负责（本文件的行内空白
   用例 0.997468 是「已脱盲但未判红」的第二个实例）。

另附一条实测的上游事实（红线）：同一页里 `Line.x0` 带着三级缩进（56.7 / 80.7 / 104.7），
而 `Line.text` 的行首空白被 pdfplumber 的 `extract_text_lines()` 剥掉了——代码块缩进在
S1 的文本层丢失，只能由 CH-482 的几何重建找回；本文件的漂移对照把重建后的缩进再去掉，
模拟「旧产物 / 排版漂移」的另一侧。
"""

from __future__ import annotations

import difflib

import pytest

from doc2md.pipeline.convert import convert
from doc2md.pipeline.metrics import _WS, char_similarity, norm_lines

pytestmark = [pytest.mark.milestone("DoD")]

GATE = 0.99                       # 方案 4.7 / G1 的等价阈值

# 产物里代码行的规范缩进（空格数）。CH-482 起产物自带按几何重建的缩进，漂移样本
# 由它逐行去掉缩进得到；命中数不足即说明样本或重建口径变了，先红再让人复核。
_CANONICAL_INDENT = {
    "def indent_probe(x):": 0,
    "if x:": 4,
    "return 1": 8,
    "return 0": 4,
    "for i in range(3):": 0,
    "print(i)": 4,
}


# ---------------------------------------------------------------- 旧口径参照实现

def _legacy_norm_lines(text: str) -> list[str]:
    """CH-475 收紧前的 `norm_lines`：删尽全部空白（缩进与行内空格一起消失）。"""
    return [s for s in (_WS.sub("", ln) for ln in (text or "").splitlines()) if s]


def _similarity(norm, a: str, b: str) -> float:
    """CH-429 算法（行锚定 + 区间字符级精比 + 长度归一化），归一化函数可换。

    用 `_similarity(norm_lines, …)` 复算的结果必须等于产品的 `char_similarity`，
    该等式由红线单独钉住——参照实现与产品实现一旦分叉，本文件的对照数字即失效。
    """
    la, lb = norm(a), norm(b)
    na, nb = sum(len(x) for x in la), sum(len(x) for x in lb)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0

    def _matched(x: str, y: str) -> int:
        if not x or not y:
            return 0
        sm = difflib.SequenceMatcher(None, x, y, autojunk=False)
        return sum(bl.size for bl in sm.get_matching_blocks())

    matched = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        if tag == "equal":
            matched += sum(len(x) for x in la[i1:i2])
        else:
            matched += _matched("".join(la[i1:i2]), "".join(lb[j1:j2]))
    return round(2.0 * matched / (na + nb), 6)


# ---------------------------------------------------------------- 漂移构造（源自真实产物）

@pytest.fixture(scope="module")
def product(golden_dir) -> str:
    return convert(golden_dir / "codeindent_1p.pdf").markdown


def _deindent(md: str) -> str:
    """去掉代码行的规范缩进（= 缩进漂移的另一侧产物：旧产物 / 顶格排版）。

    同时校验产物自身带的就是重建后的规范缩进——若重建失效（全部顶格），这里的
    `ln == " "*level + stripped` 断言先红，而不是静默把漂移样本变成原样。
    """
    hit = 0
    out: list[str] = []
    for ln in md.splitlines():
        stripped = ln.lstrip()
        level = _CANONICAL_INDENT.get(stripped)
        if level is None:
            out.append(ln)
        else:
            hit += 1
            assert ln == " " * level + stripped, (
                f"规范缩进漂移：{ln!r} 期望 {level} 个前导空格")
            out.append(stripped)
    assert hit == len(_CANONICAL_INDENT), f"缩进锚点只命中 {hit}/{len(_CANONICAL_INDENT)} 行，产物形状已变"
    return "\n".join(out)


def _marker_drift(md: str) -> str:
    """`-` 符号 bullet → `*`（内容级、单字符替换）。"""
    drifted = md.replace("- 甲项：等价性门的缩进用例锚点", "* 甲项：等价性门的缩进用例锚点")
    drifted = drifted.replace("- 乙项：符号标记可被合成为星号标记", "* 乙项：符号标记可被合成为星号标记")
    assert drifted != md, "列表标记锚点未命中，语料形状已变"
    return drifted


def _fence_loss(md: str) -> str:
    """删掉第一个代码块的成对围栏（围栏破损 = 代码块与后续正文的边界消失）。"""
    assert md.count("```") >= 2, "产物围栏不足一对，样本形状已变"
    return md.replace("```", "", 2)


def _inline_space(md: str) -> str:
    """行内空格增删（新口径按单空格折叠，旧口径直接删尽）。"""
    assert "for i in range(3):" in md
    return md.replace("for i in range(3):", "for i in range( 3 ):")


# ---------------------------------------------------------------- 红线

@pytest.mark.gate
def test_indent_drift_turns_the_gate_red_and_legacy_is_exactly_blind(product):
    """缩进漂移：收紧后的门判红，收紧前的口径精确等于 1.0（结构失明）。"""
    deindented = _deindent(product)
    assert norm_lines(product) != norm_lines(deindented), "新口径对行首缩进仍失明"
    assert char_similarity(product, deindented) < GATE, (
        f"缩进漂移未判红：{char_similarity(product, deindented)}")
    assert _similarity(_legacy_norm_lines, product, deindented) == 1.0, (
        "旧口径不再是精确失明——本文件的对照基线要重判")


@pytest.mark.gate
def test_content_drift_gains_no_extra_sensitivity_from_the_tightening(product):
    """内容级漂移（标记替换 / 围栏破损）：收紧前后差值 < 0.001，敏感度没有增加。

    这条是 CH-475 登记文案的校正：`-`↔`*` 不是「旧口径失明、新口径看得见」，
    两种口径都只按字符数吃它；真正被收紧改变的只有空白类漂移。
    """
    for name, drifted in (("marker", _marker_drift(product)), ("fence", _fence_loss(product))):
        new = char_similarity(product, drifted)
        old = _similarity(_legacy_norm_lines, product, drifted)
        assert abs(new - old) < 0.001, f"{name}：新口径 {new} 与旧口径 {old} 差值过大，口径说明失真"
        assert (new < GATE) == (old < GATE), f"{name}：两种口径的红/绿结论不一致"


@pytest.mark.gate
def test_s1_extraction_drops_the_leading_indent_that_geometry_still_carries(golden_dir):
    """上游事实（CH-478 实测，CH-482 复核）：`Line.x0` 有三级缩进，`Line.text` 的行首空白已被剥光。

    断言只落在 S1 抽取层：CH-482 在 S3 按 x0 重建缩进后，抽取层的文本仍是顶格的
    ——若哪天 pdfplumber 开始保留前导空白，S1 与 S3 就会双重缩进，本条先红提醒重判。
    """
    from doc2md.pipeline.s1_parse import read_any

    mono = [ln for page in read_any(golden_dir / "codeindent_1p.pdf")
            for ln in page.lines if ln.mono and ln.text and not ln.text.startswith("```")]
    assert mono, "等宽行未抽出，样本形状已变"
    assert all(ln.text == ln.text.lstrip() for ln in mono), (
        "抽取层已开始保留行首空白，本条登记事实需重判")
    assert len({round(ln.x0, 1) for ln in mono}) >= 3, (
        f"几何信号应有 ≥3 个缩进档位，实测 {sorted({round(ln.x0, 1) for ln in mono})}")


@pytest.mark.gate
def test_reference_similarity_stays_in_sync_with_the_product_gate(product):
    """参照实现与产品实现必须同值，否则上面的旧/新对照数字随时会失真。"""
    drifted = _deindent(product)
    assert _similarity(norm_lines, product, drifted) == char_similarity(product, drifted)


# ---------------------------------------------------------------- 判据本体（信息层）

def test_inline_whitespace_drift_is_unblinded_but_not_gate_red(product):
    """行内空格漂移：旧口径精确 1.0，新口径已脱离失明，但本语料分母大 → 未判红。

    这是「0.99 只探测粗大漂移」的实例：判红还取决于漂移量相对语料的占比，
    逐字节等值由 12.2 的 sha 对照负责，不得把本门当等值证明引用。
    """
    drifted = _inline_space(product)
    assert _similarity(_legacy_norm_lines, product, drifted) == 1.0
    new = char_similarity(product, drifted)
    assert 1.0 > new >= GATE, f"行内空白漂移应「脱盲但未判红」，实测 {new}"


def test_sample_covers_the_four_block_shapes(golden_dir):
    """样本必须同时供出 heading / paragraph / code×2 / list_item，缺一类则漂移面不成立。"""
    ir = convert(golden_dir / "codeindent_1p.pdf").ir
    kinds = {}
    for b in ir.blocks:
        kinds[b.type] = kinds.get(b.type, 0) + 1
    assert kinds.get("code") == 2, f"应有两段代码块（围栏块 + 纯等宽块），实测 {kinds}"
    assert kinds.get("list_item") == 2 and kinds.get("heading") == 1, f"结构面不足：{kinds}"
    assert kinds.get("paragraph", 0) >= 3, f"正文分母过小，漂移会被相对降幅淹没：{kinds}"


@pytest.mark.parametrize("line, expected", [
    ("    indented", "    indented"),      # 行首缩进保留（代码块语义所在）
    ("a    b", "a b"),                     # 行内连续空白折叠为单空格
    ("   ", None),                         # 纯空白行丢弃
    ("\t\ttabbed", "\t\ttabbed"),          # 制表符缩进同样算行首空白
])
def test_norm_lines_keeps_leading_indent_folds_inline_runs(line, expected):
    got = norm_lines(line)
    assert got == ([expected] if expected is not None else [])
