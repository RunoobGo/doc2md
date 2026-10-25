"""`python -m doc2md` 入口（CH-500 / R17-PKG-1）。

README 与附录 B 一直把 `python -m doc2md …` 当作可用调用形态，但包里从未有
`__main__.py`——实测 `python -m doc2md --help` 直接 `No module named doc2md.__main__`。
只有 console script（pyproject `[project.scripts] doc2md = "doc2md.cli:main"`）可用，
于是「未安装、用源码树 + venv 直接跑」的入口缺一块：`python -m doc2md` 是跨平台
最稳的调用方式（不依赖 PATH 里的 `doc2md`  shim，也不受 Windows 脚本包装器影响）。
"""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
