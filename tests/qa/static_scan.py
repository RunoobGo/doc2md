#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""static_scan.py —— 前端静态红线的取值面（CH-503 / R18-GUI-3）。

**为什么要有这个模块**：CH-469 的转义扫描用「`.innerHTML =` 后面第一个反引号段」
当模板正文。多段拼接（`tr.innerHTML = \`a\` + \`b\` + …;`）只有首段进过判据——
`renderQueue` / `renderDocs` / `renderBlocks` 三个真实汇点全是拼接形态，
把任意一段的 `esc()` 摘掉，红线照样绿。这不是「断言写得松」，是**扫描面比声明面窄**
（对照表 O-g 的同一形态），而转义红线的所有价值都在覆盖面里。

所以这里只改一件事：**按语句取，不按反引号段取**——从 `=` 扫到语句结束的 `;`，
串/模板/括号内的 `;` 不算结束。取到的语句再交给调用方的判据（裸插正则、
逐插值 esc 检查……），本模块不判断内容对错。

`interpolations()` 递归展开嵌套 `${}`，配合调用方的
`len(...) == expr.count("${")` 等式断言：嵌套花括号不能让插值静默逃出计数。
"""
from __future__ import annotations

import re

#: 语句扫描时视为「字面量」的引号（模板串里的 `${}` 整体按字面量吞，
#: 其中的 `;` 与引号不参与语句定界——宁可多吞，不可提前截断）。
_QUOTES = ("'", '"', "`")


def inner_html_statements(src: str) -> list[str]:
    """取出所有赋给 `.innerHTML` 的**右值表达式全文**（含多段拼接的全部模板段）。

    与旧口径的差别只有一个可测事实：拼接语句返回一段而不是首段。调用方必须用
    尾段锚点自证这一点（见 `tests/test_ch503_docs_panel.py`），否则本函数被换回
    首段口径时无人报警。
    """
    out: list[str] = []
    for m in re.finditer(r"\.innerHTML\s*=\s*", src):
        expr, _ = _scan_statement(src, m.end())
        out.append(expr)
    return out


def _scan_statement(src: str, i: int) -> tuple[str, int]:
    """从下标 i 扫到定句的分号（不含分号）；串内、括号内的分号不作定界。"""
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    n = len(src)
    while i < n:
        c = src[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < n:          # 反斜杠转义（含模板串内的 \`）
                buf.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in _QUOTES:
            quote = c
            buf.append(c)
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            break
        buf.append(c)
        i += 1
    return "".join(buf), i


def interpolations(expr: str) -> list[str]:
    """表达式里所有 `${...}` 的正文，嵌套的也单独列出（与 `expr.count("${")` 等量）。"""
    out: list[str] = []

    def walk(s: str) -> None:
        i = 0
        while i < len(s):
            if s.startswith("${", i):
                depth, j = 1, i + 2
                while j < len(s) and depth:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    j += 1
                body = s[i + 2:j - 1]
                out.append(body)
                walk(body)                        # 嵌套插值也必须计入
                i = j
            else:
                i += 1

    walk(expr)
    return out


def unescaped_interpolations(expr: str) -> list[str]:
    """表达式里没过 `esc()` 的插值正文（调用方自行决定哪些汇点要求全转义）。"""
    return [body for body in interpolations(expr) if "esc(" not in body]
