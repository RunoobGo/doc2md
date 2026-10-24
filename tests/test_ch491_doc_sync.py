"""CH-491（B-1 / B-2）+ CH-499：错误码、死代码与附录 F/G 的静态对账红线。

五条断言同属「静态对账」一族，与 `test_ch477_version_sync.py`
（文档 ↔ 代码）互补：

- **错误码引用**：`errors.CODE_INDEX` 里每个错误码必须在全仓至少有一处
  *实例化引用*。CH-491 之前 `E4092_SourceSuperseded` 定义了类、进了
  `CODE_INDEX`，唯一抛出点 `worker.py` 却写字面量 `"E4092"`——类/字面量
  双轨，改类属性（http 状态、detail 口径）不会传导到调用点，且无类型保护。
- **AST 零引用**：生产模块内不得存在「定义了但全仓无人调用」的函数。
  CH-491 清掉 4 处（`llm._build_payload`、`s1_5_gate._avg_chars`、
  `s1_parse._is_bold`、`s1_parse._is_italic`）；死代码会让读者误以为
  某能力已接线，也会让重构时的改动面判断失真。
- **附录 G 内部对账（CH-499，2 项）**：组头声明的「N 个模块，M 行」等于
  组内逐行之和，且采集方法段那串加法等于五个组头。CH-498 的病史是「组头
  按 `wc` 重算、逐行抄上一档」，两处当场不等却全绿入库。
- **附录 F 层级引用（CH-499，1 项）**：全文每个「第 N 层」都要在附录 F 有
  同名层标题、层号连续。CH-495 的收口注声称层表「续接……要点表见下」而该
  层从未存在——假承诺的交叉引用同样全绿入库，读者按图索骥必然落空。

断言写法沿用 CH-477 的纪律：**先确认锚点/枚举成功，再比对**——解析不到
必须红，不能静默跳过。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from doc2md import config
from doc2md.errors import CODE_INDEX

pytestmark = pytest.mark.gate

REPO_ROOT = Path(config.__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "doc2md"
TESTS_DIR = REPO_ROOT / "tests"


def _py_files() -> list[Path]:
    return [p for p in PKG_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def _all_source_text() -> str:
    """生产 + 测试全量源码文本（用于字面量/名字引用计数）。"""
    parts = [p.read_text(encoding="utf-8") for p in _py_files()]
    parts += [p.read_text(encoding="utf-8") for p in TESTS_DIR.rglob("*.py")
              if "__pycache__" not in p.parts]
    return "\n".join(parts)


def _referenced_class_names() -> set[str]:
    """全仓（除 errors.py）被真正引用到的类名集合。

    用 AST 的 `Name` 节点而非字符串匹配：`from .errors import Xxx` 在 AST 里是
    `ImportFrom.alias` 而非 `Name`，因此 import 行天然不算引用；而 `.code` 属性
    访问、`raise Xxx(...)` 实例化、`class Sub(Xxx)` 基类继承三种形态都会产生
    `Name` 节点。字符串匹配会把 import 与类定义自身误判成引用（CH-491 调试中
    实测到的两种假阴性）。
    """
    names: set[str] = set()
    paths = [p for p in _py_files() if p.name != "errors.py"]
    paths += [p for p in TESTS_DIR.rglob("*.py") if "__pycache__" not in p.parts]
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise AssertionError(f"{path}: 无法解析 {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
    return names


def test_every_error_code_has_a_real_reference() -> None:
    """CODE_INDEX 每个码至少一处**真实引用**（防「定义类、调用点写字面量」双轨）。

    口径：类名必须作为 `Name` 节点出现在非 errors.py 的源码里——覆盖
    `.code` 取常量、`raise Xxx(...)` 实例化、`class Sub(Xxx)` 基类继承三种形态；
    `from .errors import Xxx` 的 import 行不算（那正是 CH-491 之前 E4092 的形态：
    import 了类、调用点仍写字面量 `"E4092"`，改类属性不会传导到调用点）。
    """
    assert CODE_INDEX, "CODE_INDEX 为空——错误码表被清空或导入失败"
    referenced = _referenced_class_names()
    orphans = [f"{code} ({cls.__name__})" for code, cls in sorted(CODE_INDEX.items())
               if cls.__name__ not in referenced]
    assert not orphans, (
        "以下错误码在 CODE_INDEX 中登记但全仓无真实引用（类/字面量双轨复发）："
        + ", ".join(orphans))


def _top_level_plain_functions(tree: ast.Module) -> list[str]:
    """模块**顶层**、且**无装饰器**的函数名。

    收窄理由：类方法可能被继承/多态调用，带装饰器的函数由框架注册
    （Flask 路由、pytest fixture），`create_app` 内的端点闭包由
    `add_url_rule` 挂载——三者都不靠名字出现次数体现引用。CH-491 清掉的
    4 处死代码（`llm._build_payload` 等）均为顶层无装饰器函数，正是本断言
    的目标形态。
    """
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.decorator_list:
            names.append(node.name)
    return names


def test_no_unreferenced_production_functions() -> None:
    """生产模块无零引用的顶层无装饰器函数（防死代码再生）。"""
    text = _all_source_text()
    dead: list[str] = []
    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:                     # 语法错误由 ruff/导入拦，这里不重复
            raise AssertionError(f"{path}: 无法解析 {exc}") from exc
        for name in _top_level_plain_functions(tree):
            if name.startswith("__") and name.endswith("__"):
                continue                               # dunder 由解释器调用
            # 全仓出现次数 > 1（定义处之外至少一次）才算有引用。
            if text.count(name) <= 1:
                dead.append(f"{path.relative_to(REPO_ROOT)}:{name}")
    assert not dead, "以下生产函数全仓零引用（死代码）：" + ", ".join(dead)


# --- 附录 G 内部对账（CH-499）-------------------------------------------------
#
# 病史七连（附录 G 引导段自陈）全是同一个形态：改了代码或改了组头，却没改另一处。
# CH-498 那次更进一步——登记行声称「G.1 cli.py 1350→1351、G.2 batch.py 398→409」，
# 实际只重算了五个组头，两条行格原封不动，于是组头与组内逐行和当场不等。
#
# 本断言刻意**不碰 `wc -l`**：行数列随提交浮动，绑上真实源码就让门禁变成
# 「每轮必须重抄附录 G」的税（附录 G 自己写明「行数不作为断言对象」）。它只钉
# **文档自身的两处互相引用**——组头声明的模块数与行和、采集方法段那串加法——
# 这两个数与代码无关，改代码永远碰不红它，而「只改一处」必然改红它。

GROUP_RE = re.compile(r"^### G\.(\d) [^\n]*?（(\d+) 个模块，(\d+) 行）$", re.M)
ROW_RE = re.compile(r"^\| `([^`]+)`[^|]*\| (\d+) \|", re.M)
SUMMARY_RE = re.compile(
    r"五个分层小节的行数相加为 (\d+) \+ (\d+) \+ (\d+) \+ (\d+) \+ (\d+) = (\d+)")


def _appendix_g_text() -> str:
    """附录 G 的 G.1…G.6 切片（到采集方法段末尾为止）。"""
    text = (REPO_ROOT / "docs" / "dev-plan.md").read_text(encoding="utf-8")
    start = text.find("## 附录 G 模块与代码清单")
    assert start >= 0, "附录 G 标题漂移，红线锚点失效"
    end = text.find("# 登记篇", start)
    assert end > start, "附录 G 的结束锚点漂移，红线锚点失效"
    return text[start:end]


def test_appendix_g_group_headers_match_their_own_rows() -> None:
    """G.1–G.5 的组头「N 个模块，M 行」必须等于该组表格的逐行和。"""
    section = _appendix_g_text()
    groups = list(GROUP_RE.finditer(section))
    assert len(groups) == 5, f"附录 G 组头解析到 {len(groups)} 个，应为 5 个（红线锚点失效）"

    mismatched: list[str] = []
    for idx, g in enumerate(groups):
        stop = groups[idx + 1].start() if idx + 1 < len(groups) else len(section)
        # G.6 是测试资产表，先截到它之前，避免把测试文件行算进 G.5。
        g6 = section.find("### G.6", g.end(), stop)
        body = section[g.end():g6 if g6 > 0 else stop]
        rows = [(m.group(1), int(m.group(2))) for m in ROW_RE.finditer(body)
                if m.group(1) != "文件"]
        claimed_n, claimed_lines = int(g.group(2)), int(g.group(3))
        assert rows, f"{g.group(0)} 组内未解析到任何行（红线锚点失效）"
        if len(rows) != claimed_n or sum(n for _, n in rows) != claimed_lines:
            mismatched.append(
                f"{g.group(1)} 组头记 {claimed_n} 个模块 / {claimed_lines} 行，"
                f"逐行实和 {len(rows)} 个 / {sum(n for _, n in rows)} 行")
    assert not mismatched, (
        "附录 G 组头与逐行和不相等（「只改组头没改行」的病史形态）："
        + "；".join(mismatched))


@pytest.mark.gate
def test_appendix_g_collection_method_sum_matches_headers() -> None:
    """采集方法段的「a + b + c + d + e = S」必须等于五个组头，且加得对。"""
    section = _appendix_g_text()
    m = SUMMARY_RE.search(section)
    assert m is not None, "采集方法段的行数相加句格式漂移，红线锚点失效"
    stated = [int(m.group(i)) for i in range(1, 6)]
    total = int(m.group(6))

    headers = [int(g.group(3)) for g in GROUP_RE.finditer(section)]
    assert len(headers) == 5, f"附录 G 组头解析到 {len(headers)} 个，应为 5 个（红线锚点失效）"
    assert stated == headers, (
        f"采集方法段相加串 {stated} 与组头 {headers} 不同源（改了组头没改本段）")
    assert sum(stated) == total, (
        f"采集方法段五个加数之和 {sum(stated)} != 声明的合计 {total}")


# --- 附录 F 层级引用的可解析性（CH-499）---------------------------------------
#
# 同一病灶在附录 F 的对应形态是「假承诺的交叉引用」：CH-495 的收口注写下
# 「CH-495 续接第二十四层……要点表见下」，而正文的层级详表实际停在第二十三层
# （CH-494），第 24 层从未存在。读者按图索骥会落空，且这类句子是绿着入库的——
# 没有任何断言读过它。本红线只问一件事：文档里出现的每一个「第 N 层」，
# 附录 F 里必须有同名层标题；层标题自身必须从第一层连续编号、不得留空洞。

LAYER_HEAD_RE = re.compile(r"^第([一二三四五六七八九十百零]+)层：", re.M)
LAYER_ANY_RE = re.compile(r"第([一二三四五六七八九十百零]+)层")
_CN_UNITS = "一二三四五六七八九"
_CN_DIGITS = {c: i + 1 for i, c in enumerate(_CN_UNITS)}


def _cn_to_int(text: str) -> int:
    """把「二十三」这类中文层号转成整数；解析不了返回 -1（-1 必不在层号集合里）。"""
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CN_DIGITS.get(text[1], 0)
    if "十" in text:
        tens, _, ones = text.partition("十")
        return _CN_DIGITS.get(tens, 0) * 10 + _CN_DIGITS.get(ones, 0)
    return _CN_DIGITS.get(text, -1) if len(text) == 1 else -1


def _appendix_f_range(text: str) -> tuple[int, int]:
    start = text.find("## 附录 F 门禁断言清单")
    assert start >= 0, "附录 F 标题漂移，红线锚点失效"
    end = text.find("## 附录 G", start)
    assert end > start, "附录 G 的起始锚点漂移，红线锚点失效"
    return start, end


def test_appendix_f_layer_references_resolve() -> None:
    """全文每个「第 N 层」都必须命中附录 F 的层标题，且层标题连续无空洞。"""
    text = (REPO_ROOT / "docs" / "dev-plan.md").read_text(encoding="utf-8")
    lo, hi = _appendix_f_range(text)
    heads = [_cn_to_int(m.group(1)) for m in LAYER_HEAD_RE.finditer(text[lo:hi])]
    assert heads, "附录 F 未解析到任何层标题，红线锚点失效"
    assert sorted(set(heads)) == list(range(1, max(heads) + 1)), (
        f"附录 F 层标题不连续：实测 {sorted(set(heads))}，应为 1…{max(heads)}")
    declared = set(heads)
    top = max(heads)

    dangling = [f"第{m.group(1)}层" for m in LAYER_ANY_RE.finditer(text)
                if _cn_to_int(m.group(1)) not in declared]
    assert not dangling, (
        f"附录 F 的层标题止于第 {top} 层，以下「第 N 层」引用悬空（假承诺的交叉引用）："
        + "、".join(dangling))

