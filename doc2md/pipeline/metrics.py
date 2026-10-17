"""验收度量 —— 红线层「现行口径」实现（方案 4.7，登记 CH-428 / CH-429）。

char_similarity 的两处口径修正（实测驱动）：

1. **autojunk=False**（CH-428）。difflib.SequenceMatcher 默认把「出现次数 >
   len(b)/200 的元素」当 junk 跳过；中文常用字在 59 万字语料里无一不是高频字，
   直接逐字符比对会把「行级只差 5 处」的产物算成 0.34 的假失败。
2. **行锚定 + 区间内字符级精比**（CH-429）。纯逐字符 difflib 在 59 万字上
   要跑 >300s 不可用；纯定长分块对任何长度变化都会整体错位（插入一处即串位，
   实测假降到 0.07）。口径改为：先用行序列对齐（快、抗位移），
   再对每个 non-equal 区间做字符级 difflib 精比 —— 得到的仍是**字符级**匹配量，
   归一化为 2·matched/(|A|+|B|)，与方案「difflib 快速比 + 长度归一化」一致。

half_sentence_heads：块文本以「不能起句」的符号（全角逗号/顿号/句号、小写字母、
右括号引号等）开头 → 半句开头。G1 要求分片与单片都为空。
"""
from __future__ import annotations

import difflib
import re

_WS = re.compile(r"\s+")
_HEAD_CONT = re.compile(
    r"^[\u3000-\u303f，、。；：！？）】」』》”’]|\.\.\.|^[\s]*[a-z]|^[\-—_/·]")
_SENT_OPEN_OK = ("“", "「", "『", "(", "（", "【", "《")
# 拉丁正文/链接起头的块是正常句首，不因「小写字母开头」判半句
# （B-14 的小写字母规则针对 CJK 语料；URL、邮箱、缩写句为已知误报源）
_LEGIT_LOWER = re.compile(
    r"^(https?://|www\.|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}|[A-Za-z]+://)")


def norm_lines(text: str) -> list[str]:
    # R6-PIPE-2（CH-475）：原口径 `_WS.sub("", ln)` 删尽**全部**空白——等价门对
    # 「缩进丢失 / 行内空格增删」完全失明（抹掉空白后字符序列相同，门照样判绿）。
    # 改为：保留行首缩进（代码块语义所在），行内连续空白折叠为单空格。
    # 口径边界由 CH-478 批次②（`tests/test_ch478_gate_drift.py`）实测钉住：收紧只影响
    # **空白类**漂移；围栏破损与列表标记 `-`↔`*` 替换是内容级漂移，新旧口径同值（差值
    # < 0.001）——原登记把两者并列为「旧口径失明」是过度声明，不得再按那个口径立论。
    # 另注：0.99 只探测粗大漂移，判红与否取决于漂移量相对语料的分母（行内空格漂移在本
    # 语料已脱盲但未判红），逐字节等值另由 12.2 的 sha 对照负责。
    out: list[str] = []
    for ln in (text or "").splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        indent = ln[:len(ln) - len(ln.lstrip())]
        out.append(indent + _WS.sub(" ", stripped))
    return out


def _matched_chars(a: str, b: str) -> int:
    """区间内字符级匹配量（小串，成本可控）。"""
    if not a or not b:
        return 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(bl.size for bl in sm.get_matching_blocks())


def char_similarity(a: str, b: str) -> float:
    """正文字符级相似度：行锚定对齐 + 区间字符级精比 + 长度归一化。"""
    la, lb = norm_lines(a), norm_lines(b)
    na, nb = sum(len(x) for x in la), sum(len(x) for x in lb)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    matched = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            matched += sum(len(x) for x in la[i1:i2])
        else:
            matched += _matched_chars("".join(la[i1:i2]), "".join(lb[j1:j2]))
    return round(2.0 * matched / (na + nb), 6)


def is_half_head(text: str) -> bool:
    t = (text or "").strip()
    if not t or t[:1] in _SENT_OPEN_OK:
        return False
    if _LEGIT_LOWER.match(t):          # URL / 邮箱起头是完整值，不是残句
        return False
    return bool(_HEAD_CONT.match(t))


def half_sentence_heads(result) -> list[dict]:
    ir = getattr(result, "ir", result)
    return [{"id": b.id, "page": b.page, "type": b.type, "text": (b.text or "")[:60]}
            for b in ir.blocks
            if b.type in ("paragraph", "list_item") and is_half_head(b.text)]
