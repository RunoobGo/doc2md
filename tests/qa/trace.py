#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trace.py —— 需求 ↔ 测试可追溯矩阵。

回答一个此前只能靠人翻的问题：**28 条 FR、11 条 NFR、15 个 E 码、4 个 W 码、
CLI 子命令、API 端点，哪些真的有测试引用？**

三条纪律：

  1. **以代码为准**：W 码取 `doc2md.errors` 的 `WARN_*` 常量值、E 码取
     `CODE_INDEX` 的键，**并补上错误类自身的 `code` 类属性**——`E5000` 是基类
     `Doc2mdError.code`、不在 `CODE_INDEX` 里，却有 9 处合法断言引用它；漏掉会
     让孤儿红线把正常测试误判成「引用了未声明编号」。文档侧只用来抽 FR/NFR 的
     名称与优先级，不从文档表格抄码表（文档永远比代码晚一轮）。
  2. **锚点先解析再比值**：解析不到就报「锚点失效」，不许静默恒真
     （`test_ch477_version_sync` 的既定纪律）。
  3. **引用按正向上下文收集**：`assert "E5051" not in CODE_INDEX` 是「禁止复活」
     守卫，不是「引用已删除码」；含 `ast.NotIn` 的比较把两侧操作数整体标为否向，
     不进入引用集。其余比较（`==` / `in`）仍是正向——`assert code == "E4011"`
     就是最常见的覆盖证据，不能因为「比较」二字一并否掉（评审 M-3）。
  4. **符号名与字面码等价**：测试常写 `WARN_SHARD_NOT_MERGED in …` 或
     `pytest.raises(E4151_UnsupportedType)`，而不是字面 `W1044` / `E4151`。
     扫描器建别名表（`WARN_*` 常量值 + `EXXXX_*` 错误类 `code`）后再看 `Name` /
     `Attribute` 节点；基类 `Doc2mdError` 不映射到 E5000——泛捕获不等于覆盖该码。

「覆盖」只表示可追溯，不表示测得对：本模块不判定测试质量，那是 `defect_scan`
的变异自检与 `test_hard_gate` 的断言形态负责的事。

用法：
    .venv/bin/python tests/qa/trace.py --json work/qa/trace.json --md work/qa/trace.md

退出码：信息层，正常跑完恒 0（清单有未覆盖项也退 0，红线由 gate 断言承担）。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

DEV_PLAN = REPO / "docs" / "dev-plan.md"
TESTS = REPO / "tests"

RE_ID = re.compile(r"\b(FR-\d{3}|NFR-\d{1,2}|W10\d{2}|E\d{4})\b")
_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
          "delete": "DELETE"}
_E_CODE = re.compile(r"E\d{4}")
# 错误类名的形状：`E4151_UnsupportedType`。只认这个前缀模式，`Doc2mdError` /
# `ApiError` 这类基类即使 `code` 是 E5000 也不进别名表（泛捕获 ≠ 覆盖该码）。
_ERR_CLASS = re.compile(r"E\d{4}_\w+")


def declared_requirements(dev_plan: Path = DEV_PLAN) -> dict[str, dict]:
    """从 §2.1 / §2.2 的表格行抽 FR / NFR（含名称与优先级）。

    FR 行必须有优先级列；NFR 表没有优先级列，`priority` 留空串而不是伪造 P 值。
    解析数量由 gate 断言钉住（FR ≥28 / NFR ≥11），表头或列序漂移会直接转红。
    """
    out: dict[str, dict] = {}
    for ln in dev_plan.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^\|\s*(FR-\d{3})\s*\|\s*([^|]*?)\s*\|\s*(P\d)\s*\|", ln)
        if m:
            out[m.group(1)] = {"name": m.group(2), "priority": m.group(3),
                               "section": "2.1"}
            continue
        m = re.match(r"^\|\s*(NFR-\d{1,2})\s*\|\s*([^|]*?)\s*\|", ln)
        if m:
            out[m.group(1)] = {"name": m.group(2), "priority": "",
                               "section": "2.2"}
    return out


def declared_codes() -> dict[str, list[str]]:
    """W / E 码**以代码为准**（见模块 docstring 纪律 1）。

    E 码 = `CODE_INDEX` 的键 ∪ 错误类 `code` 类属性。后者专为 `E5000` 存在：
    基类不注册进 `CODE_INDEX`，但 API 兜底路径与多条测试都合法引用它。
    """
    import doc2md.errors as errors

    ws = sorted({v for k, v in vars(errors).items()
                 if k.startswith("WARN_") and isinstance(v, str)})
    es = set(errors.CODE_INDEX)
    for value in vars(errors).values():
        if isinstance(value, type) and issubclass(value, Exception):
            code = getattr(value, "code", "")
            if isinstance(code, str) and _E_CODE.fullmatch(code):
                es.add(code)
    return {"W": ws, "E": sorted(es)}


def cli_subcommands() -> list[str]:
    """AST 扫 `doc2md/cli.py` 的 `add_parser("x")`（含嵌套子命令）。"""
    tree = ast.parse((REPO / "doc2md" / "cli.py").read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_parser"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.append(node.args[0].value)
    return sorted(set(out))


def api_endpoints() -> list[str]:
    """AST 扫 `doc2md/api.py` 的 `@app.<verb>("path")` → `"GET /docs"` 形态。"""
    tree = ast.parse((REPO / "doc2md" / "api.py").read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            verb = _VERBS.get(dec.func.attr)
            if (verb and getattr(dec.func.value, "id", "") == "app"
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                out.append(f"{verb} {dec.args[0].value}")
    return sorted(set(out))


def alias_table() -> dict[str, str]:
    """符号 → 编号别名表（以代码为准，不从文档抄）。

    两类别名：

      1. `WARN_*` 常量名 → 值（`WARN_SHARD_NOT_MERGED` → `W1044`）；
      2. 形如 `EXXXX_*` 的错误类名 → 其 `code` 类属性
         （`E4151_UnsupportedType` → `E4151`）。

    **不收录基类**：`Doc2mdError`（乃至 `ApiError`）的 `code` 虽是 E5000，
    但 `pytest.raises(Doc2mdError)` 只是泛捕获，不等于断言了 E5000 这条码；
    把基类映射进去会让 E5000 的覆盖证据凭空出现（评审 N1/N2）。
    """
    import doc2md.errors as errors

    out: dict[str, str] = {}
    for name, value in vars(errors).items():
        if name.startswith("WARN_") and isinstance(value, str):
            out[name] = value
        elif _ERR_CLASS.fullmatch(name) and isinstance(value, type):
            code = getattr(value, "code", "")
            if isinstance(code, str) and _E_CODE.fullmatch(code):
                out[name] = code
    return out


def _collect_ids(node: ast.AST, aliases: dict[str, str], *,
                 in_notin: bool = False) -> set[str]:
    """递归收集一棵 AST 子树里的编号引用；`in_notin=True` 表示整棵子树否向。

    - 字符串常量 → `RE_ID.findall`（字面编号，docstring 也算——P0 需求的索引
      就写在测试 docstring 里，见 `test_ch501_trace_gaps.py`）；
    - `ast.Name.id` / `ast.Attribute.attr` 命中别名表 → 映射后的编号；
    - `ast.Compare` 只要含 `ast.NotIn`，**两侧操作数**都标否向、不收集：
      `assert "E5051" not in CODE_INDEX` 是删码守卫。普通 `==` / `in` 比较
      仍是正向，否则 `assert body["code"] == "E4011"` 这类最常见证据会被误删
      （评审 M-3：旧实现按整函数减负向集合，正负同现时连正向引用一起丢）。
    """
    ids: set[str] = set()
    negative = in_notin
    if isinstance(node, ast.Compare):
        negative = in_notin or any(isinstance(op, ast.NotIn) for op in node.ops)
    elif not in_notin:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            ids.update(RE_ID.findall(node.value))
        elif isinstance(node, ast.Name):
            if node.id in aliases:
                ids.add(aliases[node.id])
        elif isinstance(node, ast.Attribute):
            if node.attr in aliases:
                ids.add(aliases[node.attr])
    for child in ast.iter_child_nodes(node):
        ids |= _collect_ids(child, aliases, in_notin=negative)
    return ids


def _add_refs(out: dict[str, list[str]], ref: str, ids: set[str]) -> None:
    """把一次作用域的引用并入 `编号 → [ref, …]`。"""
    for ident in sorted(ids):
        out.setdefault(ident, []).append(ref)


def _scan_class(cls: ast.ClassDef, rel: str, aliases: dict[str, str],
                out: dict[str, list[str]], prefix: str = "") -> None:
    """类式用例：递归下钻 `ClassDef.body`，`test_*` 方法按 `文件::类::方法` 记名。

    旧实现只遍历 `tree.body` 的顶层函数，类内用例全部漏扫（评审 M-1）；
    嵌套类沿 `Outer::Inner` 继续下钻，复名不会互相覆盖。
    """
    qual = f"{prefix}{cls.name}"
    for stmt in cls.body:
        if isinstance(stmt, ast.ClassDef):
            _scan_class(stmt, rel, aliases, out, prefix=f"{qual}::")
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.name.startswith("test_"):
                _add_refs(out, f"{rel}::{qual}::{stmt.name}",
                          _collect_ids(stmt, aliases))


def _module_docstring(stmt: ast.stmt, index: int) -> bool:
    """模块 docstring = 第 0 条语句的裸字符串表达式。"""
    return (index == 0 and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))


def _rel_path(path: Path) -> str:
    """相对仓库根的展示路径；仓库外/相对 `tests_dir` 回退到绝对路径，不抛错。"""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def scan_references(tests_dir: Path = TESTS) -> dict[str, list[str]]:
    """编号 → 引用它的测试（`文件::函数` / `文件::类::方法` / `文件::<module>`）。

    统一走 `_collect_ids`：字面编号、`WARN_*` 常量名、`EXXXX_*` 错误类名三种写法
    等价；函数体 + 每个装饰器（`@pytest.mark.parametrize([...])` 里的编号算该
    测试的引用），类式方法同样入 `refs`。

    模块级引用只扫**顶层语句**（pytestmark / 常量表 / 参数化列表），并且排除
    模块 docstring 与函数/类定义：直接扫「所有非缩进行」会把 docstring 与注释
    一并算进来，`test_t9_convergence` 的「已删死代码 E5051」列表就会变成假覆盖。

    解析失败不静默：`SyntaxError` 打 stderr（文件名 + 异常）后继续扫其它文件，
    否则一个坏文件会让它的全部引用凭空消失，制造假 uncovered。

    名字刻意**不叫 `test_*`**：本文件长在 `tests/` 树下，而 `pytest tests/qa/trace.py`
    这种直接指定文件的调用会无视 `python_files = test_*.py` 去收集 `test_*` 函数——
    一个恒真的「测试」就这样混进证据链。
    """
    aliases = alias_table()
    out: dict[str, list[str]] = {}
    for path in sorted(tests_dir.rglob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError as exc:
            print(f"[trace] 跳过无法解析的测试文件 {path}: {exc}", file=sys.stderr)
            continue
        rel = _rel_path(path)
        for fn in tree.body:
            if isinstance(fn, ast.ClassDef):
                _scan_class(fn, rel, aliases, out)
                continue
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("test_"):
                continue
            _add_refs(out, f"{rel}::{fn.name}", _collect_ids(fn, aliases))
        # 模块级引用（pytestmark / 常量表 / 参数化列表）单独归到 ::<module>
        for index, stmt in enumerate(tree.body):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if _module_docstring(stmt, index):
                continue
            _add_refs(out, f"{rel}::<module>", _collect_ids(stmt, aliases))
    return out


def _ratio(hit: int, total: int) -> float:
    """覆盖率比值；分母为 0 时记 0.0（不是 1.0——空集不能冒充全绿）。"""
    return round(hit / total, 4) if total else 0.0


def trace(*, dev_plan: Path | None = None) -> dict:
    """汇总可追溯矩阵。`uncovered` 的五组键与 `orphans` 是 T6 的读取契约。"""
    reqs = declared_requirements(dev_plan or DEV_PLAN)
    codes = declared_codes()
    cli = cli_subcommands()
    api = api_endpoints()
    refs = scan_references()

    declared = set(reqs) | set(codes["W"]) | set(codes["E"])
    orphans = sorted(k for k in refs if k not in declared)
    fr_ids = [k for k in reqs if k.startswith("FR-")]
    nfr_ids = [k for k in reqs if k.startswith("NFR-")]
    uncovered = {
        "FR_P0": sorted(k for k in fr_ids
                        if reqs[k]["priority"] == "P0" and k not in refs),
        "FR_other": sorted(k for k in fr_ids
                           if reqs[k]["priority"] != "P0" and k not in refs),
        "NFR": sorted(k for k in nfr_ids if k not in refs),
        "W": sorted(k for k in codes["W"] if k not in refs),
        "E": sorted(k for k in codes["E"] if k not in refs),
    }
    return {
        "requirements": reqs, "codes": codes, "cli": cli, "api": api,
        "refs": {k: sorted(set(v)) for k, v in refs.items()},
        "uncovered": uncovered, "orphans": orphans,
        "coverage": {
            "FR": _ratio(sum(1 for k in fr_ids if k in refs), len(fr_ids)),
            "NFR": _ratio(sum(1 for k in nfr_ids if k in refs), len(nfr_ids)),
            "W": _ratio(sum(1 for k in codes["W"] if k in refs), len(codes["W"])),
            "E": _ratio(sum(1 for k in codes["E"] if k in refs), len(codes["E"])),
        },
    }


def _table(rows: list[tuple[str, ...]], head: tuple[str, ...]) -> list[str]:
    """Markdown 表格；无行时写一句「无」比空表更能证明扫描真的跑过。"""
    if not rows:
        return ["无"]
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def report_md(payload: dict) -> str:
    """人类可读报告：FR（按 P0/P1/P2）/ NFR / W·E / 孤儿四张表 + COVERAGE 末行。"""
    reqs = payload["requirements"]
    unc = payload["uncovered"]
    cov = payload["coverage"]
    fr_ids = [k for k in reqs if k.startswith("FR-")]
    nfr_ids = [k for k in reqs if k.startswith("NFR-")]
    n_w, n_e = len(payload["codes"]["W"]), len(payload["codes"]["E"])
    lines = [
        "# Doc2md 需求 ↔ 测试可追溯矩阵（自动生成）", "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 声明分母：FR {len(fr_ids)} / NFR {len(nfr_ids)} / "
        f"W {n_w} / E {n_e}",
        f"- 已覆盖：FR {len(fr_ids) - len(unc['FR_P0']) - len(unc['FR_other'])} / "
        f"NFR {len(nfr_ids) - len(unc['NFR'])} / "
        f"W {n_w - len(unc['W'])} / E {n_e - len(unc['E'])}",
        f"- CLI 子命令 {len(payload['cli'])} 个 / API 端点 {len(payload['api'])} 个"
        "（覆盖判定用字面量扫描，不进 uncovered）",
        "",
        "## FR 未覆盖（按优先级分组）", "",
    ]
    fr_all = unc["FR_P0"] + unc["FR_other"]
    for priority in ("P0", "P1", "P2"):
        rows = [(k, reqs[k]["name"], reqs[k]["section"])
                for k in fr_all if reqs[k]["priority"] == priority]
        lines += [f"### {priority}", ""]
        lines += _table(rows, ("编号", "名称", "章节"))
        lines.append("")
    lines += ["## NFR 未覆盖", ""]
    lines += _table([(k, reqs[k]["name"]) for k in unc["NFR"]],
                    ("编号", "名称"))
    lines += ["", "## W / E 码未覆盖", ""]
    lines += _table([("W", k, "") for k in unc["W"]]
                    + [("E", k, "") for k in unc["E"]],
                    ("类别", "编号", "备注"))
    lines += ["", "## 孤儿编号（tests/ 引用但未声明）", ""]
    lines += _table([(k,) for k in payload["orphans"]], ("编号",))
    lines += [
        "",
        "## 覆盖口径（分母 / 精确值 / 打出值）", "",
        f"- FR：{len(fr_ids) - len(unc['FR_P0']) - len(unc['FR_other'])}"
        f"/{len(fr_ids)} = {cov['FR']}",
        f"- NFR：{len(nfr_ids) - len(unc['NFR'])}/{len(nfr_ids)} = {cov['NFR']}",
        f"- W：{n_w - len(unc['W'])}/{n_w} = {cov['W']}",
        f"- E：{n_e - len(unc['E'])}/{n_e} = {cov['E']}",
        "",
        f"COVERAGE FR={cov['FR']} NFR={cov['NFR']} W={cov['W']} E={cov['E']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Doc2md 需求 / 错误码 / CLI / API ↔ 测试引用（信息层）",
        epilog="运行结果恒 0；清单本身只报告，不阻断。")
    ap.add_argument("--json", default=None, help="写 JSON 结果路径")
    ap.add_argument("--md", default=None, help="写 Markdown 报告路径")
    args = ap.parse_args()

    payload = trace()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cov = payload["coverage"]
    unc = payload["uncovered"]

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON  -> {p}")

    if args.md:
        p = Path(args.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report_md(payload), encoding="utf-8")
        print(f"Markdown -> {p}")

    print("=" * 60)
    print(f"  需求 {len(payload['requirements'])} 条｜W {len(payload['codes']['W'])} / "
          f"E {len(payload['codes']['E'])}｜CLI {len(payload['cli'])} / "
          f"API {len(payload['api'])}")
    print(f"  未覆盖 FR_P0 {len(unc['FR_P0'])}｜FR_other {len(unc['FR_other'])}｜"
          f"NFR {len(unc['NFR'])}｜W {len(unc['W'])}｜E {len(unc['E'])}｜"
          f"孤儿 {len(payload['orphans'])}")
    print(f"COVERAGE FR={cov['FR']} NFR={cov['NFR']} W={cov['W']} E={cov['E']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
