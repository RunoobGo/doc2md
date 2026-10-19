"""CH-491（B-1 / B-2）：错误码引用与死代码的静态对账红线。

两条断言同属「代码 ↔ 代码」静态对账一族，与 `test_ch477_version_sync.py`
（文档 ↔ 代码）互补：

- **错误码引用**：`errors.CODE_INDEX` 里每个错误码必须在全仓至少有一处
  *实例化引用*。CH-491 之前 `E4092_SourceSuperseded` 定义了类、进了
  `CODE_INDEX`，唯一抛出点 `worker.py` 却写字面量 `"E4092"`——类/字面量
  双轨，改类属性（http 状态、detail 口径）不会传导到调用点，且无类型保护。
- **AST 零引用**：生产模块内不得存在「定义了但全仓无人调用」的函数。
  CH-491 清掉 4 处（`llm._build_payload`、`s1_5_gate._avg_chars`、
  `s1_parse._is_bold`、`s1_parse._is_italic`）；死代码会让读者误以为
  某能力已接线，也会让重构时的改动面判断失真。

断言写法沿用 CH-477 的纪律：**先确认锚点/枚举成功，再比对**——解析不到
必须红，不能静默跳过。
"""
from __future__ import annotations

import ast
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
