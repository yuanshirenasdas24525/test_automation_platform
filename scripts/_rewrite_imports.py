"""一次性批量替换 import：把 `src.<sub>` 映射到重构后的新包路径。

映射规则（按优先级）：
  src.api              → platform.api
  src.services         → platform.services
  src.captcha_solver   → core.captcha_solver
  src.core             → core
  src.database         → database
  src.runners          → runners
  src.utils            → utils
  src.common           → common

  另外兼容 ParameterCache 旧路径 src.core.mobile.cache...（已自然覆盖）。

用法：python scripts/_rewrite_imports.py        # 默认对整个仓库执行
     python scripts/_rewrite_imports.py --dry  # 只打印 diff 不写回
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# 顺序很关键：更长的前缀要先匹配
MAPPINGS = [
    ("src.api",            "platform.api"),
    ("src.services",       "platform.services"),
    ("src.captcha_solver", "core.captcha_solver"),
    ("src.core",           "core"),
    ("src.database",       "database"),
    ("src.runners",        "runners"),
    ("src.utils",          "utils"),
    ("src.common",         "common"),
]

# 跳过的路径
SKIP_DIRS = {"venv", ".git", "node_modules", "__pycache__", "dist", "build",
             ".idea", ".vscode", "frontend", "data", "docs", "docker"}
# docs 里有示意代码但不参与代码执行，doc 替换容易误伤，跳过
SKIP_FILES: set[str] = set()


def iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        parts = set(p.parts)
        if parts & SKIP_DIRS:
            continue
        if p.name in SKIP_FILES:
            continue
        yield p


def rewrite_content(text: str) -> tuple[str, int]:
    """在一个文件里做替换，返回 (新内容, 替换次数)。"""
    total = 0
    for old, new in MAPPINGS:
        # from src.X import Y   —— 允许行首任意缩进（函数内局部 import 很常见）
        pat_from = re.compile(rf"(?m)^(\s*from\s+){re.escape(old)}(\b)")
        text, n1 = pat_from.subn(rf"\1{new}\2", text)
        # import src.X (as Z)?
        pat_import = re.compile(rf"(?m)^(\s*import\s+){re.escape(old)}(\b)")
        text, n2 = pat_import.subn(rf"\1{new}\2", text)
        total += n1 + n2
    return text, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="只打印不写回")
    parser.add_argument("root", nargs="?", default=".", help="项目根，默认当前目录")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    changed_files = 0
    total_subs = 0
    for py in iter_py_files(root):
        original = py.read_text(encoding="utf-8")
        new_text, n = rewrite_content(original)
        if n == 0:
            continue
        changed_files += 1
        total_subs += n
        print(f"{py.relative_to(root)}: {n} 处替换")
        if not args.dry:
            py.write_text(new_text, encoding="utf-8")
    print(f"\n总计：{changed_files} 个文件，{total_subs} 处替换"
          f"{'（dry-run，未写回）' if args.dry else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
