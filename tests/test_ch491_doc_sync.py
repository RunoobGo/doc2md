"""CH-491（B-1 / B-2）+ CH-499 + CH-503 + CH-504 + CH-507：错误码、死代码与文档口径的静态对账红线。

八条断言同属「静态对账」一族，与 `test_ch477_version_sync.py`
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
- **覆盖率三写可复算（CH-503，1 项）**：活文档里每处「精确 X%」都要能由紧邻的
  「分母 / 未覆盖」现场算出来。v3.34 的三写口径只规定「怎么记」，没规定「记得
  对不对」——本轮实测抓到两处继承错（分母从 740 换成 738、百分比仍抄 90.51%，
  现场复算是 90.53%），与附录 G 组头是同一个病灶。
- **交叉引用可解析（CH-504，1 项）**：每处 `some/file.py` 里的 `symbol` 都要能解析到
  那个符号。本轮实测两例悬空引用：一例正是「改档承诺无人看守」的那条反向红线，
  另一例在活文档里给一条真实存在的红线记了一个从未出现过的名字。详见文末该节病史。
- **行号引用棘轮（CH-507 / OPT4-19，1 项）**：活文档里 `文件.py:NNN` 形态的命中数
  不得超过冻结常量（初始 173 = dev-plan 72 + technical-reference 101）。行号随代码
  漂移后引用**静默指错**——比断链更糟：读者会读到一段无关代码并把它当成依据。
  历史轮次报告与计划文档是取证快照，行号正是其证据价值的一部分，**不进分母**。

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


# --- 覆盖率三写可复算（CH-503 / R18-DOC-1）------------------------------------
#
# v3.34 立的口径要求「任何会被取整的指标须同时给出分母 + 精确值 + 打出值，
# 每次收口回读产生它的那份日志」。它管住了形态，没管住算术：本轮实测抓到两处
# 「分母换了、精确值没跟着换」的继承错——README 与 technical-reference 都把
# CH-501 的 `7797 stmts / 738 miss` 记成精确 90.51%（那是上一档 740 miss 的值），
# 现场复算是 90.53%。病灶与附录 G 组头同源：**组合口径必须能由它的加数复算**。
#
# 配对窗口取 260 字符：既要容下「TOTAL 7,804 stmts / 738 miss = 精确 90.54%」
# 这种分母紧跟精确值的写法，也要容下 v3.34 那处双档并列（`7797 stmts / 740 miss`
# 打出 91%、精确 90.51%；轮中 `7800/743` 打出 90%、精确 90.47%）——两档必须各自
# 配到自己的分母，所以短形态（反引号里的 A/B）同样进配对集。

TRIO_FULL_RE = re.compile(r"(\d[\d,]*)\s+stmts?\s*/\s*(\d[\d,]*)\s+miss")
TRIO_SHORT_RE = re.compile(r"`(\d[\d,]*)\s*/\s*(\d[\d,]*)`")
TRIO_EXACT_RE = re.compile(r"精确\s*\*{0,2}(\d{2}\.\d{2})%")
TRIO_WINDOW = 260

#: 只钉「当前对外发布的数」。历史存档（`docs/qa/**`、各轮审查报告）保留当时
#: 时点的记法，按 12.3 的「旧数字并列保留、不因新数字删除旧记录」不改写。
LIVING_DOCS = ("README.md", "docs/dev-plan.md", "docs/technical-reference.md")


def _trio_violations(text: str) -> tuple[list[str], int]:
    """返回（不可复算的「精确 X%」清单，成功配对并复核的条数）。"""
    pairs = sorted((m.start(), int(m.group(1).replace(",", "")),
                    int(m.group(2).replace(",", "")))
                   for rx in (TRIO_FULL_RE, TRIO_SHORT_RE) for m in rx.finditer(text))
    out: list[str] = []
    checked = 0
    for m in TRIO_EXACT_RE.finditer(text):
        stated = float(m.group(1))
        near = [p for p in pairs if p[0] < m.start() <= p[0] + TRIO_WINDOW]
        if not near:
            out.append(f"精确 {stated}% 在 {TRIO_WINDOW} 字符内找不到「分母 / 未覆盖」对（三写缺一写）")
            continue
        checked += 1
        _, stmt, miss = near[-1]
        exact = round((1 - miss / stmt) * 100, 2)
        if abs(exact - stated) > 0.005:
            out.append(f"精确 {stated}% 与 {stmt} stmts / {miss} miss 的复算值 {exact}% 不符")
    return out, checked


def test_coverage_trio_is_recomputable() -> None:
    """活文档里每个「精确 X%」都必须由紧邻的分母 / 未覆盖对现场复算出来。"""
    violations: list[str] = []
    total = 0
    for rel in LIVING_DOCS:
        found, checked = _trio_violations((REPO_ROOT / rel).read_text(encoding="utf-8"))
        total += checked
        violations += [f"{rel}: {v}" for v in found]
    # 显式前置：扫不到样本就是锚点漂移，不能绿着过（同 R18-QA-1 的纪律）。
    assert total >= 6, f"三写复核面只剩 {total} 处（<6），正则锚点漂移即视为红线失效"
    assert not violations, (
        "覆盖率精确值不可复算（分母换了、百分比没跟着换）：\n  " + "\n  ".join(violations))
    # 负夹具：助手自己必须报得出红——它不红就是又一个空跑。
    bad, n = _trio_violations("TOTAL 7,804 stmts / 738 miss，精确 90.51%")
    assert n == 1 and bad, "三复算助手对已知错值报绿（助手本身失效）"
    orphan, m = _trio_violations("打出 91%、精确 90.54%")
    assert m == 0 and orphan, "三写助手对「只有百分比、没有分母」报绿（缺式即漏）"


# --- 交叉引用可解析（CH-504 / R19-DOC-1）--------------------------------------
#
# 同族第三例。CH-495 抓的是「第 N 层」指向不存在的层标题，CH-503 抓的是「精确 X%」
# 配不上自己的分母，本轮抓的是 `path/to/file.py` 里的 `symbol` 指向不存在的符号。三例的共同
# 点：**交叉引用是一份承诺，而这份承诺过去没人核**。
#
# 本轮实测两例，都不是无害笔误：
# ① `tests/qa/mdscore.py` 把 MD040 改档的反向红线记作
#    `test_md040_reverts_to_contract_if_lang_lands`（真名
#    `test_md040_class_tracks_product_lang_capability`）。按名 grep 得到空结果，于是
#    「若 S3 将来带上 lang，本档必须改回 contract」这个承诺看起来无人看守——而它正是
#    改档能被批准的前提。同轮 `defect_scan.py` 的 docstring 把 MD040 的权威记作
#    `MARKDOWN_SPEC`（仓库里从来没有这个符号）是同一病灶的更严重形态：那份引用一度
#    是「MD040 属契约类」的唯一依据，并污染了 evaluate / attribute / prioritize 三层。
# ② `docs/dev-plan.md` §12.7 的 B-27 行把 v10 迁移红线记作
#    `test_db_v10_migration_backfills_lease_columns`；真名
#    `test_db_v10_adds_lease_columns_and_migrates_old_library` 在三份活文档里**一次都
#    没出现过**（改前实测 `grep -c` = 0）。该行同时写明了这条红线「用 `ALTER TABLE …
#    DROP COLUMN` 造出 v9 形态、重开补齐、再开仍幂等」——描述与真名下的断言逐条相符，
#    错的只有名字。所以它比 ① 更隐蔽：内容是对的，读者会以为是自己的 grep 姿势不对。
#
# 只扫活文档与本仓代码，不扫 `docs/qa/**` 与各轮审查报告：那些是历史存档，按 §12.3
# 「旧记录不因新数字删除」保留当时时点的写法；拿今天的符号表去判它们红，等于强迫改写
# 档案，也会把「当时的引用后来失效」这个事实抹掉。
#
# 上面两个错名**故意**不带 `::` 写成裸名：带了就会让本节判自己的病史红。这不是给
# 本文件开后门——负夹具用的是真文件配假符号，助手照样报得出红。
#
# **盲区（本轮自己踩到，写明）**：本节只认「`某文件.py` + 双冒号 + 符号名」这一种写法
# （此处刻意不写出完整形态：本文件自己也在 tests/ 扫描面内，写出即被自己判成悬空引用）。
# 裸反引号名
# （`` `_row_to_block` ``）不在扫描面里，而本轮实施 R19-API-6 时新写的注释正是这么
# 引用了一个不存在的函数——真名是 `list_blocks` 内联的逐字段反序列化，仓库里没有
# `_row_to_block` 这个符号。把裸名也纳入会误伤大量普通行文（反引号在本仓文档里也用于
# 配置键、字段名、命令），故本节维持窄口径；裸名引用仍靠评审与 `grep` 兜。

CITE_RE = re.compile(r"([\w./\-]+\.py)::(\w+)")
CITE_DIRS = ("doc2md", "tests", "ops", "config")


def _defined_names(path: Path) -> set[str]:
    """文件里可被 `::` 引用的名字：函数 / 类 / 赋值目标（含带注解与嵌套的）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _py_index() -> tuple[dict[str, list[Path]], dict[Path, set[str]]]:
    """裸文件名 → 候选路径；路径 → 已定义名（缓存，避免同一文件重复解析）。"""
    index: dict[str, list[Path]] = {}
    cache: dict[Path, set[str]] = {}
    for d in CITE_DIRS:
        for p in (REPO_ROOT / d).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            index.setdefault(p.name, []).append(p)
            cache[p] = _defined_names(p)
    return index, cache


def _cite_violations(text: str, index: dict[str, list[Path]],
                     cache: dict[Path, set[str]]) -> tuple[list[str], int]:
    """返回（解析不到的引用清单，成功扫到的引用条数）。"""
    out: list[str] = []
    n = 0
    for m in CITE_RE.finditer(text):
        n += 1
        rel, sym = Path(m.group(1)), m.group(2)
        direct = REPO_ROOT / rel
        cands = [direct] if direct.exists() else index.get(rel.name, [])
        if not cands:
            out.append(f"{rel}::{sym}：仓库里没有这个文件")
            continue
        for c in cands:
            cache.setdefault(c, _defined_names(c))
        if not any(sym in cache[c] for c in cands):
            out.append(f"{rel}::{sym}：{'、'.join(str(c.relative_to(REPO_ROOT)) for c in cands)}"
                       f" 里没有这个符号")
    return out, n


def test_cross_references_resolve() -> None:
    """每处 `some/file.py` 里的 `name` 都必须能在仓库里解析到那个符号。"""
    index, cache = _py_index()
    assert index, "四个代码目录一个 .py 都没扫到——红线锚点失效"
    texts = {rel: (REPO_ROOT / rel).read_text(encoding="utf-8") for rel in LIVING_DOCS}
    for d in CITE_DIRS:
        for p in sorted((REPO_ROOT / d).rglob("*.py")):
            if "__pycache__" not in p.parts:
                texts[str(p.relative_to(REPO_ROOT))] = p.read_text(encoding="utf-8")

    violations: list[str] = []
    total = 0
    for rel, text in texts.items():
        found, n = _cite_violations(text, index, cache)
        total += n
        violations += [f"{rel}: {v}" for v in found]
    # 显式前置：CH-504 复核实测 **51** 处（三份活文档 37 + 代码 14）。原下限 20 是
    # CH-491 那轮的口径，此后本轮把 dev-plan 附录 C 与 doctor 段共 13 处 `文件.py:NNN`
    # 行号引用改写成符号引用（R19-API-2 / R19-API-3：行号引用无从机器复核、且实测
    # 10/13 已漂移），条数只增不减。下限随之抬到 40——留着 20 就等于允许活文档把
    # 半数符号引用退回成不可验证的行号，而这条红线看不出来。
    assert total >= 40, f"交叉引用只剩 {total} 处（<40），正则或扫描面漂移即视为红线失效"
    assert not violations, (
        "悬空的交叉引用（假承诺：读者按名 grep 必然落空）：\n  " + "\n  ".join(violations))
    # 负夹具：助手自己必须报得出红——两种形态（文件在而符号不在、文件根本不在）都要。
    # 两个字面量都**拆开拼**：本节要扫 `tests/` 下每个 .py 的正文，写成一个完整串就会
    # 被自己扫到、判自己红。拆开后正则在本文件里匹配不到，运行时拼出的值照旧是坏引用。
    sep = "::"
    bad_sym, k = _cite_violations("tests/qa/mdscore.py" + sep + "no_such_rule", index, cache)
    assert k == 1 and bad_sym, "引用助手对「文件在、符号不在」报绿（助手本身失效）"
    bad_file, j = _cite_violations("tests/test_nope.py" + sep + "test_x", index, cache)
    assert j == 1 and bad_file, "引用助手对「文件不存在」报绿（助手本身失效）"



# --- 行号引用棘轮（CH-507 / OPT4-19 / B-50）----------------------------------
#
# 活文档的指位纪律是「`文件.py` + 双冒号 + 符号名」：行号随代码漂移，漂移后引用
# **静默指错**——读者会读到一段无关代码并把它当成依据。历史轮次报告与计划文档
# （`docs/review-round*.md`、`docs/pm-review-*.md`、`docs/spec-*.md`、
# `docs/superpowers/plans/*.md`）是取证快照，行号正是其证据价值的一部分，
# **不进本棘轮分母**——改掉等于篡改病史。
#
# 存量只减不增：每把一处 `文件.py:NNN` 改写成符号引用（文件名 + 双冒号 + 符号名），
# 就把 LINE_REF_LIMIT 下调一处。新增引用一律用符号——本断言只防增长，不主动收敛。
LINE_REF_RE = re.compile(r"[A-Za-z0-9_./-]+\.py:[0-9]{2,4}")
LINE_REF_LIMIT = 173


def test_living_docs_line_number_references_do_not_grow() -> None:
    """活文档的 `文件.py:NNN` 引用数不得超过冻结上限（只减不增）。"""
    counts = {rel: len(LINE_REF_RE.findall(
        (REPO_ROOT / rel).read_text(encoding="utf-8"))) for rel in LIVING_DOCS}
    total = sum(counts.values())
    assert total <= LINE_REF_LIMIT, (
        f"活文档行号引用 {total} 处 > 冻结上限 {LINE_REF_LIMIT}（只减不增）。"
        f"分项：{counts}。新增指位请用符号引用（文件名 + 双冒号 + 符号名）；"
        "存量每改一处把 LINE_REF_LIMIT 下调一处。")
    # 负夹具：正则对已知行号形态必须命中——否则计数恒 0，棘轮恒真。
    assert LINE_REF_RE.findall("见 doc2md/api.py:123 与 l2.py:45") == [
        "doc2md/api.py:123", "l2.py:45"], "行号引用正则对已知形态无命中（棘轮本身失效）"
