#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assertions.py —— 反空跑断言助手（OPT-2 / 对照表 O-a·O-b·O-c·G6）。

**为什么要有这个模块**：QA 第三轮 111 项探针首跑有 4 项给出假信号（3.6%）。四个
病灶长得不同，抽象出来是同一件事——判定式里含一个让断言在「什么都没测」时也成立的
前件：`if len(ids) >= 2` 让翻页用例在命中不足时静默通过；只断言 `rc == 0` 让 0 命中
也算过；用裸子串判「裸标签」被产品自己的 `\\<script>` 转义误判；端点数用 22/19/21
三种分母混比。产品侧的同构缺陷是 N3。

所以这里只补一样东西：**「取到了样本」必须是显式前置，不足即 FAIL**。两个助手都不
判断内容对错，因此不保证断言有意义（对照表 O-g：样本在、断言面只覆盖声明的一半）——
把它们当成万能豁免正是本模块要防的另一种退化。

放在 `tests/qa/` 而不是 `tests/conftest.py`：助手本身必须有红线钉住「负夹具必须 FAIL」，
而 conftest 不是测试可正常导入的模块。用法与既有 `_need`/`_missing`（CH-471 / TEST-1）
同族——那两条守的是「资产缺失不得静默 skip」，这两条守的是「取样不足不得静默通过」。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

import pytest

#: 「未被反斜杠转义的 `<`」前缀。产品红线（`doc2md/pipeline/s4_render.py`
#: `_contract_violation`）用的就是这个 lookbehind；测试侧任何「裸标签」判定都必须
#: 同源，否则 CH-500 之后的转义产物会被自家探针误报成注入缺陷。
UNESCAPED_LT = r"(?<!\\)<"


def bare_tag_re(*tags: str) -> re.Pattern[str]:
    """构造「未转义的裸标签」正则：`bare_tag_re("script", "iframe")`。

    统一走这里，而不是每条探针再手写一遍子串判断——`"<script" in md` 会把
    `\\<script>`（已转义、正是红线要求的形态）误判成命中。刻意不加 `\\b`：
    产品红线 `doc2md/pipeline/s4_render.py::_ANCHOR_PATTERNS` 就是这个形态，
    两侧多一个字符类就会在「转义口径对账」红线上分叉。
    """
    alt = "|".join(t.lower() for t in tags)
    return re.compile(rf"{UNESCAPED_LT}(?:{alt})", re.I)


def _materialize(rows: Any, subject: str) -> list:
    if isinstance(rows, list):
        return rows
    if isinstance(rows, dict | set | frozenset | tuple):
        return list(rows)
    if isinstance(rows, Iterable):
        return list(rows)
    raise AssertionError(f"{subject}：取样对象不可计数（{type(rows).__name__}）——判据写错了")


def require_hits(rows: Any, minimum: int, subject: str) -> list:
    """取样口径守卫：少于 `minimum` 条即 FAIL，并把口径打进失败信息。返回条目列表。

    `minimum` 是「这个断言至少要看几条样本才有意义」，不是期望值——所以「恰好 0 条」
    和「比预期少」都是红灯，而不是空转通过。生成器会被消费成列表（消费即取样）。
    """
    items = _materialize(rows, subject)
    if len(items) < minimum:
        pytest.fail(f"反空跑（OPT-2）：{subject} 取样 {len(items)} 条 < 要求 {minimum} 条"
                    f"——判定式在样本不足时会空真，故必须显式失败。"
                    f"先核对取样口径（来源、过滤条件、标记），再改判据。")
    return items


def assert_no_vacuous(items: Any, check: Callable[[Any], Any], *, subject: str = "循环") -> int:
    """「循环体至少执行过一次」的通用形态：空集合 FAIL，逐项 `check` 不许吞异常。

    `check` 返回假值即视为该项不通过；抛异常也按不通过处理，但异常信息保留——两者都
    带下标报出，避免「22 篇里第 7 篇不对」退化成「有个地方不对」。返回条目数。
    """
    rows = require_hits(items, 1, f"{subject} 的待检集合")
    bad: list[str] = []
    for i, item in enumerate(rows):
        try:
            ok = check(item)
        except Exception as exc:  # noqa: BLE001 —— 逐项失败必须归因到下标，不能整批中断
            bad.append(f"[{i}] {item!r} → 抛 {type(exc).__name__}: {exc}")
            continue
        if not ok:
            bad.append(f"[{i}] {item!r} → 判据为假")
    if bad:
        pytest.fail(f"反空跑（OPT-2）：{subject} {len(rows)} 项中 {len(bad)} 项不通过：\n  "
                    + "\n  ".join(bad[:20]))
    return len(rows)
