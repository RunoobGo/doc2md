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
"""
from __future__ import annotations

import re

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+")
_LATIN_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")


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
    q = (query or "").strip()
    if not q:
        return ""
    terms: list[str] = []
    for run in _CJK_RUN.finditer(q):
        s = run.group()
        if len(s) == 1:
            terms.append(s)
        else:
            terms.extend(s[i:i + 2] for i in range(len(s) - 1))
    covered = {m.span() for m in _CJK_RUN.finditer(q)}
    rest = "".join(ch for i, ch in enumerate(q)
                   if not any(i >= a and i < b for a, b in covered))
    for w in _LATIN_WORD.findall(rest):
        terms.append('"' + w.replace('"', '""') + '"')
    return " ".join(terms)


def query_terms(query: str) -> list[str]:
    """查询拆成必须**连续出现**的子串（CJK 串 + 拉丁词），用于 FTS 命中后的精确校验。

    FTS5 的 n-gram AND 语义只保证各字组存在，不保证相邻顺序（「例外」「情况」
    分处两段也会命中）；用 LIKE 复核可把这类假阳性清零。
    """
    q = (query or "").strip()
    terms: list[str] = []
    covered: set[tuple[int, int]] = set()
    for m in _CJK_RUN.finditer(q):
        terms.append(m.group())
        covered.add(m.span())
    rest = "".join(ch for i, ch in enumerate(q)
                   if not any(i >= a and i < b for a, b in covered))
    for w in _LATIN_WORD.findall(rest):
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
