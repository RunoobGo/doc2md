"""CJK 检索分词辅助（M9a 验收：编辑提交后 ≤5s 可检索到新内容）。

问题（实测发现，登记 CH-435）：FTS5 默认 `unicode61` 把连续 CJK 当作**一个整词**
token，因此「例外」「本条款涉及」这类子串查询命中 0 —— 语料以中文为主时，
搜索面与「编辑后可检索」验收实际是失效的。

做法（不引入外部分词器，保持离线纪律）：
- 索引侧：额外维护 `aux` 列，写入 CJK 连续串的 **1 字元 + 2 字元 n-gram**（空格分隔），
  unicode61 会把每个 n-gram 当独立 token；原文列 `text` 保持原样供 snippet 展示。
- 查询侧：把查询拆成 CJK 串与拉丁词；CJK 串长度 ≥2 → 展开成相邻 bigram，
  FTS5 默认 AND 语义近似「连续短语」；长度 1 → 用 unigram；拉丁词加引号精确匹配。

代价：索引体积约 2 倍于纯 CJK 文本；离线单用户场景可接受。

CH-459（A-9）：两处鲁棒性修正——① covered-span 从「逐字符 × 跨度集合 any()」
（O(n×m)）改为**区间掩码一次遍历**；② 查询串截断上限 `MAX_QUERY_CHARS`：
超长查询（粘贴整段文档）会把 FTS 表达式撑到解析器失控，截断到上限后
FTS 与 LIKE 两条路径同口径（都按前缀语义），`search` 的既有兜底分工不变。
"""
from __future__ import annotations

import re

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+")
_LATIN_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")

# CH-459（A-9）：查询串截断上限。正常查询远小于此值；超长输入（如误粘贴
# 整篇文档）截断到该长度——FTS bigram 表达式与 LIKE 前缀同口径降级。
MAX_QUERY_CHARS = 256


def _clip(query: str) -> str:
    return (query or "").strip()[:MAX_QUERY_CHARS]


def _non_cjk_rest(q: str) -> str:
    """去掉被 CJK 段覆盖的字符，剩余部分交拉丁词提取。

    CH-459（A-9）：原实现逐字符 `any(i >= a and i < b for a, b in covered)`
    是 O(字符数 × CJK 段数)；改布尔掩码一次遍历，语义不变。
    """
    mask = [True] * len(q)
    for m in _CJK_RUN.finditer(q):
        for i in range(m.start(), m.end()):
            mask[i] = False
    return "".join(ch for i, ch in enumerate(q) if mask[i])


def cjk_aux(text: str) -> str:
    """全文 → 检索辅助列：CJK 串展开为 unigram+bigram，非 CJK 段原样保留。

    非 CJK 段（拉丁词/数字）原样带上，是为了覆盖「条款ZZ」这类 CJK 紧邻拉丁的
    混合 token —— unicode61 在 text 列里会把它切成一个整词，查询「ZZ」无法命中。
    """
    out: list[str] = []
    t = text or ""
    pos = 0
    for m in _CJK_RUN.finditer(t):
        seg = t[pos:m.start()]
        if seg.strip():
            out.append(seg)
        pos = m.end()
        run = m.group()
        out.extend(run)                                   # unigram（单字查询兜底）
        if len(run) >= 2:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    tail = t[pos:]
    if tail.strip():
        out.append(tail)
    return " ".join(out)


def build_match(query: str) -> str:
    """用户查询 → FTS5 MATCH 表达式；无法构造时返回空串（调用方走 LIKE 兜底）。"""
    q = _clip(query)
    if not q:
        return ""
    terms: list[str] = []
    for run in _CJK_RUN.finditer(q):
        s = run.group()
        if len(s) == 1:
            terms.append(s)
        else:
            terms.extend(s[i:i + 2] for i in range(len(s) - 1))
    for w in _LATIN_WORD.findall(_non_cjk_rest(q)):
        terms.append('"' + w.replace('"', '""') + '"')
    return " ".join(terms)


def query_terms(query: str) -> list[str]:
    """查询拆成必须**连续出现**的子串（CJK 串 + 拉丁词），用于 FTS 命中后的精确校验。

    FTS5 的 n-gram AND 语义只保证各字组存在，不保证相邻顺序（「例外」「情况」
    分处两段也会命中）；用 LIKE 复核可把这类假阳性清零。
    CH-459（A-9）：与 `build_match` 同口径截断（`MAX_QUERY_CHARS`）——
    两条路径对同一查询必须给出一致的子串集，否则 FTS 候选与 LIKE 复核错位。
    """
    q = _clip(query)
    terms: list[str] = []
    for m in _CJK_RUN.finditer(q):
        terms.append(m.group())
    for w in _LATIN_WORD.findall(_non_cjk_rest(q)):
        terms.append(w)
    seen: set[str] = set()
    return [t for t in terms if not (t.lower() in seen or seen.add(t.lower()))]


def like_escape(term: str) -> str:
    """LIKE 通配符转义（配合 `ESCAPE '\\'`），避免用户输入 % _ 被当通配。"""
    out = []
    for ch in term:
        if ch in ("%", "_", "\\"):
            out.append("\\")
        out.append(ch)
    return "".join(out)
