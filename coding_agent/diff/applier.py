"""diff 应用器 —— 把 LLM 结构化 changes 应用到代码文件。

核心职责：
1. ``changes_to_unified_diff``：把 LLM 输出的结构化 changes（``[{file, action, ...}]``）
   转为标准 unified diff 文本，供前端 DiffViewer 展示
2. ``apply_change_text``：把单个 change 应用到文件文本（基于文本锚点匹配）
3. ``apply_changes_to_working_tree``：把 changes 直接写入工作树文件

输入约定：
- ``changes`` 是 ``list[dict]``，每个 dict 含：
  - ``file`` (str)：目标文件路径（相对 repo 根）
  - ``action`` (str)：``append`` | ``insert_after`` | ``insert_before`` | ``replace``
  - ``code`` (str)：要写入的新代码
  - ``find_text`` (str)：replace 时的匹配锚点
  - ``after_text`` / ``before_text`` (str)：insert 时的位置锚点
"""
from __future__ import annotations

import difflib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# change → 文本
# ---------------------------------------------------------------------------

def apply_change_text(change: dict, text: str) -> str:
    """把单个结构化 change 应用到文本，返回修改后的文本。

    支持的 action：
    - ``append``：在文件末尾追加 code
    - ``insert_after``：在 after_text 之后插入 code
    - ``insert_before``：在 before_text 之前插入 code
    - ``replace``：把 find_text 替换为 code

    匹配失败时抛 ``ValueError``。
    """
    action = change.get("action", "")
    code = change.get("code", "")

    if action == "append":
        return text.rstrip("\n") + "\n" + code.rstrip("\n") + "\n"

    if action in ("insert_after", "insert_before"):
        anchor_key = "after_text" if action == "insert_after" else "before_text"
        anchor = change.get(anchor_key, "")
        if not anchor:
            raise ValueError(f"{action} 缺少 {anchor_key}")
        index = text.find(anchor)
        if index == -1:
            # 容错：移除末尾换行后重试
            stripped_anchor = anchor.rstrip("\n")
            index = text.find(stripped_anchor)
        if index == -1:
            raise ValueError(f"{action} 未找到匹配文本: {anchor[:50]}...")
        if action == "insert_after":
            insert_pos = index + len(anchor)
            if insert_pos < len(text) and text[insert_pos] == "\n":
                insert_pos += 1
        else:
            insert_pos = index
            if insert_pos > 0 and text[insert_pos - 1] != "\n":
                code = "\n" + code
        code = code.rstrip("\n") + "\n"
        return text[:insert_pos] + code + text[insert_pos:]

    if action == "replace":
        find_text = change.get("find_text", "")
        if not find_text:
            raise ValueError("replace 缺少 find_text")
        index = text.find(find_text)
        if index == -1:
            raise ValueError(f"replace 未找到匹配文本: {find_text[:50]}...")
        code = code.rstrip("\n") + "\n"
        return text[:index] + code + text[index + len(find_text):]

    return text


# ---------------------------------------------------------------------------
# changes → unified diff
# ---------------------------------------------------------------------------

def changes_to_unified_diff(changes: list[dict], repo_dir: Path) -> str:
    """把 LLM 输出的结构化 changes 转为 unified diff 文本。

    工作流程：
    1. 按 ``file`` 字段分组
    2. 对每组 changes，读原始文件 → 依次 apply → 取 ``difflib.unified_diff``
    3. 拼接所有文件的 diff 为一个字符串

    Args:
        changes: LLM 输出的变化列表
        repo_dir: 仓库工作目录根

    Returns:
        标准 unified diff 字符串；无有效变更时返回空字符串
    """
    # 按文件分组
    file_changes: dict[str, list[dict]] = {}
    for c in changes:
        fp = c.get("file", "")
        if not fp:
            continue
        file_changes.setdefault(fp, []).append(c)

    diff_parts: list[str] = []
    for fp, fp_changes in file_changes.items():
        full_path = repo_dir / fp
        if not full_path.exists():
            logger.warning(f"changes_to_unified_diff: 文件不存在 {fp}，跳过")
            continue
        try:
            original = full_path.read_text(encoding="utf-8")
        except Exception:
            logger.warning(f"changes_to_unified_diff: 读取 {fp} 失败，跳过")
            continue

        modified = original
        errors: list[str] = []
        for c in fp_changes:
            try:
                modified = apply_change_text(c, modified)
            except ValueError as exc:
                errors.append(str(exc))
                continue

        if modified == original:
            if errors:
                logger.warning(f"changes_to_unified_diff: {fp} 部分改动失败: {'; '.join(errors)}")
            continue
        if errors:
            logger.warning(f"changes_to_unified_diff: {fp} 部分改动失败但继续: {'; '.join(errors)}")

        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                modified.splitlines(keepends=True),
                fromfile=f"a/{fp}",
                tofile=f"b/{fp}",
            )
        )
        if diff_lines:
            diff_parts.append("".join(diff_lines))

    return "\n".join(diff_parts)


# ---------------------------------------------------------------------------
# changes → 工作树文件
# ---------------------------------------------------------------------------

def apply_changes_to_working_tree(
    changes: list[dict],
    repo_dir: Path,
) -> tuple[list[str], list[str]]:
    """把 LLM 结构化 changes 直接写入工作树文件。

    Args:
        changes: LLM 输出的变化列表
        repo_dir: 仓库工作目录根

    Returns:
        ``(success_files, failed_files)``  —— 成功和失败的文件路径列表
    """
    # 按文件分组
    file_changes: dict[str, list[dict]] = {}
    for c in changes:
        fp = c.get("file", "")
        if not fp:
            continue
        file_changes.setdefault(fp, []).append(c)

    success: list[str] = []
    failed: list[str] = []

    for fp, fp_changes in file_changes.items():
        full_path = repo_dir / fp

        if not full_path.exists():
            # 仅 append 可以创建新文件
            if all(c.get("action") == "append" for c in fp_changes):
                full_path.parent.mkdir(parents=True, exist_ok=True)
                text = ""
            else:
                failed.append(f"{fp}（文件不存在）")
                continue
        else:
            try:
                text = full_path.read_text(encoding="utf-8")
            except Exception:
                failed.append(f"{fp}（读取失败）")
                continue

        modified = text
        file_errors: list[str] = []
        for c in fp_changes:
            try:
                modified = apply_change_text(c, modified)
            except ValueError as exc:
                file_errors.append(str(exc))

        if modified == text and not file_errors:
            # 无实际变更
            continue

        # 即使部分失败也写入（部分成功好过全失败）
        try:
            full_path.write_text(modified, encoding="utf-8")
            if file_errors:
                failed.append(f"{fp}（部分失败: {'; '.join(file_errors)}）")
            else:
                success.append(fp)
        except Exception as exc:
            failed.append(f"{fp}（写入失败: {exc}）")

    return success, failed
