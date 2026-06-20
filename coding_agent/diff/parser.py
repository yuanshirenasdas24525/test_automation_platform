"""unified diff 解析器 —— 把 LLM / ``git diff`` 输出的 diff 文本解析为结构化数据。

产物：
- ``DiffFile``：单个文件的 diff（含文件路径、创建/删除标记、hunks 列表）
- ``DiffHunk``：单个区段（行号区间 + 带前缀的行列表）

支持格式：
- 标准 ``git diff`` / ``diff -u`` 输出
- LLM 生成的简化 unified diff（可能省略 ``index`` / ``mode`` 行）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class DiffHunk:
    """diff 中的一个区段。

    ``lines`` 每行保留前缀：``" "`` 上下文、``"+"`` 新增、``"-"`` 删除。
    """
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)

    @property
    def added_lines(self) -> list[str]:
        """仅新增行（去掉 + 前缀）。"""
        return [line[1:] for line in self.lines if line.startswith("+")]

    @property
    def removed_lines(self) -> list[str]:
        """仅删除行（去掉 - 前缀）。"""
        return [line[1:] for line in self.lines if line.startswith("-")]


@dataclass
class DiffFile:
    """单个文件的 diff 结果。"""
    old_path: str
    new_path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False

    @property
    def is_rename(self) -> bool:
        return self.old_path != self.new_path and not self.is_new and not self.is_deleted


# ---------------------------------------------------------------------------
# 正则常量
# ---------------------------------------------------------------------------

_FILE_HEADER_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$")
_OLD_MODE_RE = re.compile(r"^old mode \d+$")
_NEW_MODE_RE = re.compile(r"^new mode \d+$")
_DELETED_MODE_RE = re.compile(r"^deleted file mode \d+$")
_NEW_MODE2_RE = re.compile(r"^new file mode \d+$")
_INDEX_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+")
_RENAME_FROM_RE = re.compile(r"^rename from (.*)$")
_RENAME_TO_RE = re.compile(r"^rename to (.*)$")
_SIMILARITY_RE = re.compile(r"^similarity index \d+%$")
_BINARY_RE = re.compile(r"^Binary files .* differ$")
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*)$"
)
_NEW_FILE_RE = re.compile(r"^new file mode \d+$")
_DELETE_FILE_RE = re.compile(r"^deleted file mode \d+$")


# ---------------------------------------------------------------------------
# 主解析函数
# ---------------------------------------------------------------------------

def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    """把 unified diff 文本解析为 ``DiffFile`` 列表。

    解析策略：
    1. 按 ``diff --git`` 行切分文件
    2. 每个文件内按 ``@@ ... @@`` 切分 hunk
    3. 忽略无法解析的垃圾行（如 LLM 在 diff 前后的说明文本）

    返回空列表的情况：
    - 输入为空字符串
    - 不包含任何合法 diff 块
    """
    if not diff_text or not diff_text.strip():
        return []

    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: DiffHunk | None = None

    # 是否正在 diff 块内部（过了文件头但还没到 hunk 头）
    in_file_body = False

    lines = diff_text.splitlines(keepends=False)

    for line in lines:
        # ── diff --git 行：新文件开始 ──
        m = _FILE_HEADER_RE.match(line)
        if m:
            # 保存上一个文件
            if current_file is not None:
                _finish_file(current_file, current_hunk, files)
            current_file = DiffFile(old_path=m.group(1), new_path=m.group(2))
            current_hunk = None
            in_file_body = True
            continue

        # ── 在 diff 块内但还没有文件头 → 可能是 LLM 省略了 diff --git 行 ──
        if current_file is None:
            if line.startswith("--- a/") or line.startswith("+++ b/"):
                # 可能是简化 diff 格式：--- a/path\n+++ b/path
                pass
            continue

        # ── 文件元信息行 ──
        if _OLD_MODE_RE.match(line) or _NEW_MODE_RE.match(line):
            continue
        if _INDEX_RE.match(line):
            continue
        if _NEW_FILE_RE.match(line):
            current_file.is_new = True
            continue
        if _DELETE_FILE_RE.match(line) or _DELETED_MODE_RE.match(line):
            current_file.is_deleted = True
            continue
        if _RENAME_FROM_RE.match(line):
            continue
        if _RENAME_TO_RE.match(line):
            continue
        if _SIMILARITY_RE.match(line):
            continue

        # ── Binary 文件 ──
        if _BINARY_RE.match(line):
            current_file.is_binary = True
            in_file_body = False
            continue

        # ── --- / +++ 行：跳过（路径信息已在 diff --git 行取过）──
        if line.startswith("--- ") or line.startswith("+++ "):
            continue

        # ── hunk 头 ──
        m = _HUNK_HEADER_RE.match(line)
        if m:
            if current_hunk is not None and current_file is not None:
                current_file.hunks.append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            current_hunk = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            continue

        # ── hunk 内的行 ──
        if current_hunk is not None:
            if line.startswith(" ") or line.startswith("+") or line.startswith("-"):
                current_hunk.lines.append(line)
            # 空行也可能是上下文（\"\" 不带前缀）
            elif line == "":
                current_hunk.lines.append(" ")
            continue

    # 收尾最后一个文件
    _finish_file(current_file, current_hunk, files)

    return files


def _finish_file(
    current_file: DiffFile | None,
    current_hunk: DiffHunk | None,
    files: list[DiffFile],
) -> None:
    if current_file is None:
        return
    if current_hunk is not None:
        current_file.hunks.append(current_hunk)
    files.append(current_file)


# ---------------------------------------------------------------------------
# 便捷方法
# ---------------------------------------------------------------------------

def get_changed_files(diff_text: str) -> list[str]:
    """从 diff 文本中提取变更的文件路径列表。"""
    files = parse_unified_diff(diff_text)
    return [f.new_path for f in files if not f.is_deleted]


def diff_stats(diff_text: str) -> dict:
    """返回 diff 的统计信息：文件数、新增行、删除行。"""
    files = parse_unified_diff(diff_text)
    total_added = 0
    total_removed = 0
    for df in files:
        for hunk in df.hunks:
            total_added += len([l for l in hunk.lines if l.startswith("+")])
            total_removed += len([l for l in hunk.lines if l.startswith("-")])
    return {
        "files_changed": len(files),
        "lines_added": total_added,
        "lines_removed": total_removed,
    }
