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
    return [_WS.sub("", ln) for ln in (text or "").splitlines() if _WS.sub("", ln)]


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
