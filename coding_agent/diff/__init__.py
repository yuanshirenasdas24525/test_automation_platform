"""diff 子模块 —— unified diff 解析 / apply / dry-run。

三个文件：
- ``parser``   ：把 unified diff 文本解析为结构化 ``DiffFile / DiffHunk``
- ``applier``  ：把 LLM 结构化 changes 转为 unified diff，或应用 diff 到文件
- ``validator``：diff 应用前校验（dry-run，检测冲突）

不 import SQLAlchemy session / FastAPI 对象 —— 纯函数，方便测试。
"""

from coding_agent.diff.parser import (
    DiffFile,
    DiffHunk,
    parse_unified_diff,
)
from coding_agent.diff.applier import (
    apply_changes_to_working_tree,
    changes_to_unified_diff,
    apply_change_text,
)
from coding_agent.diff.validator import (
    can_apply_cleanly,
    dry_run_changes,
)

__all__ = [
    "DiffFile",
    "DiffHunk",
    "parse_unified_diff",
    "apply_changes_to_working_tree",
    "changes_to_unified_diff",
    "apply_change_text",
    "can_apply_cleanly",
    "dry_run_changes",
]
