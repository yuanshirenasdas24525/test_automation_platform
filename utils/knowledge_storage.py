"""知识库文件本地存储 —— 阶段 3。

落盘 data/knowledge/<project_id>/<uuid><ext>；扩展名白名单 + 50MB 上限 + uuid 命名。
storage_path 存「相对 data/ 的路径」（如 knowledge/7/abcd.pdf），便于迁移。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Tuple

_ROOT = Path(__file__).resolve().parent.parent   # utils/ 的上一级 = 仓库根
_DATA = _ROOT / "data"

MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

ALLOWED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv",
    ".ppt", ".pptx", ".md", ".txt", ".json",
}


def safe_ext(filename: str) -> str:
    """取小写扩展名；不在白名单返回 ''（调用方据此拒绝）。"""
    ext = Path(filename or "").suffix.lower()
    return ext if ext in ALLOWED_EXTS else ""


def is_allowed(filename: str) -> bool:
    return safe_ext(filename) != ""


def within_size(size: int) -> bool:
    return 0 < size <= MAX_SIZE_BYTES


def save_bytes(project_id: int, filename: str, data: bytes) -> Tuple[str, int]:
    """存字节，返回 (相对 data/ 的存储路径, 字节数)。扩展名白名单由调用方保证。"""
    ext = Path(filename or "").suffix.lower()
    rel_dir = Path("knowledge") / str(project_id)
    (_DATA / rel_dir).mkdir(parents=True, exist_ok=True)
    rel_path = str(rel_dir / f"{uuid.uuid4().hex}{ext}")
    (_DATA / rel_path).write_bytes(data)
    return rel_path, len(data)


def abs_path(storage_path: str) -> Path:
    return _DATA / storage_path


def delete_file(storage_path: str) -> None:
    try:
        p = abs_path(storage_path)
        if p.is_file():
            p.unlink()
    except Exception:  # noqa: BLE001 —— 磁盘删除失败不阻断 DB 事务
        import logging
        logging.getLogger(__name__).warning("删除知识库文件失败：%s", storage_path)
