"""diff 校验器 —— 在正式 apply 前做 dry-run 检查。

两类校验：
1. ``can_apply_cleanly``：检查 unified diff 能否干净应用到工作树（无冲突）
2. ``dry_run_changes``：检查结构化 changes 应用时会不会报错（不实际写文件）
"""
from __future__ import annotations

import logging
from pathlib import Path

from coding_agent.diff.applier import apply_change_text
from coding_agent.diff.parser import parse_unified_diff

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# unified diff dry-run
# ---------------------------------------------------------------------------

def can_apply_cleanly(diff_text: str, repo_dir: Path) -> tuple[bool, list[str]]:
    """检查 unified diff 能否干净应用到工作树。

    流程：
    1. 解析 diff → ``DiffFile`` 列表
    2. 对每个文件的每个 hunk，模拟 apply 看锚点行是否匹配
    3. 记录所有不匹配的 hunk

    Args:
        diff_text: unified diff 文本
        repo_dir: 仓库工作目录根

    Returns:
        ``(ok, conflicts)``：
        - ``ok``：True 表示可干净应用
        - ``conflicts``：冲突描述列表（每个描述一个不匹配的 hunk）
    """
    files = parse_unified_diff(diff_text)
    if not files:
        return False, ["无法解析 diff 文本"]

    conflicts: list[str] = []

    for df in files:
        full_path = repo_dir / df.new_path

        if df.is_new:
            if full_path.exists():
                conflicts.append(f"{df.new_path}: 新文件但目标路径已存在")
            continue

        if df.is_deleted:
            if not full_path.exists():
                conflicts.append(f"{df.new_path}: 要删除但文件不存在")
            continue

        if not full_path.exists():
            conflicts.append(f"{df.new_path}: 文件不存在")
            continue

        try:
            original = full_path.read_text(encoding="utf-8")
        except Exception:
            conflicts.append(f"{df.new_path}: 读取失败")
            continue

        original_lines = original.splitlines()

        for i, hunk in enumerate(df.hunks):
            # 用上下文行 + 删除行做锚点匹配
            anchor_lines: list[str] = []
            for line in hunk.lines:
                if line.startswith(" ") or line.startswith("-"):
                    anchor_lines.append(line[1:])

            if not anchor_lines:
                continue

            # 尝试在原始文件中找到锚点序列的位置
            match_start = _find_sequence(original_lines, anchor_lines, hunk.old_start - 1)
            if match_start == -1:
                ctx_preview = "\n".join(anchor_lines[:3])
                conflicts.append(
                    f"{df.new_path} hunk {i + 1} (@@ -{hunk.old_start},{hunk.old_count}): "
                    f"上下文不匹配，前几行:\n{ctx_preview[:200]}"
                )

        # 验证 hunk 内部一致性（新增行数和删除行数是否匹配头信息）
        for i, hunk in enumerate(df.hunks):
            actual_removed = sum(1 for l in hunk.lines if l.startswith("-"))
            actual_added = sum(1 for l in hunk.lines if l.startswith("+"))
            if actual_removed != hunk.old_count:
                conflicts.append(
                    f"{df.new_path} hunk {i + 1}: 声明删除 {hunk.old_count} 行，实际 {actual_removed}"
                )
            if actual_added != hunk.new_count:
                conflicts.append(
                    f"{df.new_path} hunk {i + 1}: 声明新增 {hunk.new_count} 行，实际 {actual_added}"
                )

    return len(conflicts) == 0, conflicts


def _find_sequence(
    lines: list[str],
    sequence: list[str],
    start_hint: int = 0,
) -> int:
    """在 ``lines`` 中搜索 ``sequence`` 的首次出现位置。

    从 ``start_hint`` 行开始搜索（hunk 头给的 old_start 作为提示），
    如果在 hint 位置附近 ±50 行找不到，回退全文件搜索。
    """
    if not sequence:
        return 0

    # 先试 hint 位置附近
    search_start = max(0, start_hint - 10)
    search_end = min(len(lines), start_hint + 50)
    idx = _search_window(lines, sequence, search_start, search_end)
    if idx != -1:
        return idx

    # 回退全文件搜索
    idx = _search_window(lines, sequence, 0, len(lines))
    return idx


def _search_window(
    lines: list[str],
    sequence: list[str],
    start: int,
    end: int,
) -> int:
    """在 lines[start:end] 内搜索 sequence 的首次出现。"""
    seq_len = len(sequence)
    for i in range(start, end - seq_len + 1):
        match = True
        for j, seq_line in enumerate(sequence):
            if lines[i + j] != seq_line:
                match = False
                break
        if match:
            return i
    return -1


# ---------------------------------------------------------------------------
# 结构化 changes dry-run
# ---------------------------------------------------------------------------

def dry_run_changes(
    changes: list[dict],
    repo_dir: Path,
) -> tuple[bool, list[str]]:
    """对结构化 changes 做 dry-run：检查每个 change 的锚点能否匹配。

    Args:
        changes: LLM 输出的变化列表
        repo_dir: 仓库工作目录根

    Returns:
        ``(ok, errors)``：
        - ``ok``：True 表示所有 change 可应用
        - ``errors``：错误描述列表
    """
    # 按文件分组
    file_changes: dict[str, list[dict]] = {}
    for c in changes:
        fp = c.get("file", "")
        if not fp:
            continue
        file_changes.setdefault(fp, []).append(c)

    errors: list[str] = []

    for fp, fp_changes in file_changes.items():
        full_path = repo_dir / fp

        if not full_path.exists():
            # append 可以创建新文件
            if all(c.get("action") == "append" for c in fp_changes):
                continue
            errors.append(f"{fp}: 文件不存在且非 append 操作")
            continue

        try:
            text = full_path.read_text(encoding="utf-8")
        except Exception:
            errors.append(f"{fp}: 读取失败")
            continue

        current = text
        for i, c in enumerate(fp_changes):
            try:
                current = apply_change_text(c, current)
            except ValueError as exc:
                errors.append(f"{fp} change [{i}]: {exc}")

    return len(errors) == 0, errors
