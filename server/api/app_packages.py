"""/api/app_packages/* 路由。

App 安装包仓库：让用户在前端「App 包管理」页面上传 .apk / .ipa，平台保存到
data/app_packages/，记录元信息到 app_packages 表，后续 step 编辑器 / RunDialog
里有「选包」下拉，免去手粘文件路径的麻烦。

API 一览：
  - POST   /api/app_packages           上传新包（multipart/form-data）
  - GET    /api/app_packages           列出（可按 platform / project_id 过滤）
  - GET    /api/app_packages/{id}      取详情
  - DELETE /api/app_packages/{id}      删除（同时删磁盘文件，失败不阻断）
  - GET    /api/app_packages/{id}/download   下载原文件（前端验证用，非必需）

落盘策略：
  - 文件名加时间戳前缀防冲突：data/app_packages/{ts}_{原文件名}
  - 上传时校验扩展名只允许 .apk / .ipa
  - 上传体积上限 1GB，超了直接 413
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from server.api.deps import DBDep
from database.models import AppPackage
from config.settings import ProjectPaths

router = APIRouter(prefix="/app_packages", tags=["app_packages"])


# 落盘根目录：锚定 ProjectPaths.BASE_DIR，不依赖 cwd 启动位置。
#
# 历史坑：之前用 `Path("data/app_packages")`（cwd 相对），uvicorn 从 server/ 子
# 目录启动时文件落到 server/data/app_packages/...，DB 里存的绝对路径包含
# server/ 段；之后再从项目根启动 / 把 server/data 清掉 / Celery worker cwd
# 不一致都会让 Appium "does not exist or is not accessible"。
_STORAGE_DIR = ProjectPaths.APP_PACKAGES_DIR
# 体积上限。1GB 足以覆盖绝大多数 ipa；超了 413 提示用户想想是不是误传了视频。
_MAX_BYTES = 1024 * 1024 * 1024


def _ensure_storage_dir() -> Path:
    """惰性建目录。第一次上传前不需要存在；运行时确认即可。

    返回**绝对路径**。Appium server 的工作目录跟 FastAPI 进程不一定一样
    （典型场景：Appium 由 GUI 启动，cwd 是 ~ 或 /，而 FastAPI 是在项目根 cd 进去
    跑的），存相对路径会出现"FastAPI 找得到但 Appium 找不到"的诡异错。
    """
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return _STORAGE_DIR.resolve()


def _abs_path(p: Optional[str]) -> str:
    """把 file_path 字段统一兜底成绝对路径，并尽量"自愈"成可用路径。

    解析顺序：
      1. 路径本身是绝对的且文件存在 → 原样返回
      2. 相对路径 → 锚定 BASE_DIR 解析；存在则返回
      3. 上面都失败 → 取 basename 在 APP_PACKAGES_DIR 里找一次（自愈兜底）。
         这是给老数据的：之前 cwd 相对路径锚定时存的绝对路径里可能多了
         "server/" 段，文件其实就在新的 APP_PACKAGES_DIR 下，按 basename
         捞回来即可，不必让用户重新上传。
      4. 还是没有 → 返回原值，让上层报错时带原始路径，方便排查。
    """
    if not p:
        return ""
    pp = Path(p)
    # 1) 绝对路径直接试
    if pp.is_absolute() and pp.exists():
        return str(pp)
    # 2) 相对路径锚 BASE_DIR
    if not pp.is_absolute():
        candidate = (ProjectPaths.BASE_DIR / pp).resolve()
        if candidate.exists():
            return str(candidate)
    # 3) basename 自愈：标准存储目录下找同名文件
    try:
        storage = _STORAGE_DIR.resolve() if _STORAGE_DIR.exists() else None
    except OSError:
        storage = None
    if storage is not None:
        candidate = storage / pp.name
        if candidate.exists():
            return str(candidate)
    # 4) 兜底：原样返回（要么仍是绝对路径，要么是 BASE_DIR 拼出来的；让上层看路径自查）
    if pp.is_absolute():
        return str(pp)
    return str((ProjectPaths.BASE_DIR / pp).resolve())


def _sniff_platform(file_name: str, override: Optional[str]) -> str:
    """优先用用户传的 platform，否则按扩展名猜：.apk → android，.ipa → ios。"""
    if override:
        v = override.strip().lower()
        if v in ("android", "ios"):
            return v
    suffix = Path(file_name).suffix.lower()
    if suffix == ".apk":
        return "android"
    if suffix == ".ipa":
        return "ios"
    raise HTTPException(status_code=400, detail="只允许上传 .apk 或 .ipa 文件")


def _serialize(pkg: AppPackage) -> dict:
    return {
        "id": pkg.id,
        "name": pkg.name,
        "file_name": pkg.file_name,
        # file_path 兜底转绝对路径：兼容历史相对路径数据（Appium 找不到的根因）
        "file_path": _abs_path(pkg.file_path),
        "platform": pkg.platform,
        "app_package": pkg.app_package,
        "bundle_id": pkg.bundle_id,
        "version": pkg.version,
        "file_size": pkg.file_size or 0,
        "project_id": pkg.project_id,
        "description": pkg.description,
        "upload_time": pkg.upload_time.isoformat() if pkg.upload_time else None,
    }


@router.post("")
async def upload_app_package(
    db: DBDep,
    file: UploadFile = File(...),
    name: str = Form(...),
    platform: Optional[str] = Form(None),
    app_package: Optional[str] = Form(None),
    bundle_id: Optional[str] = Form(None),
    version: Optional[str] = Form(None),
    project_id: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
):
    """上传一个 .apk / .ipa。文件会被落到 data/app_packages 下，元信息入库。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名缺失")

    plat = _sniff_platform(file.filename, platform)

    # 流式读到内存。1GB 上限对单进程来说能扛，量级再大就要换 streaming-to-disk。
    contents = await file.read()
    if len(contents) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {_MAX_BYTES // (1024*1024)}MB 上限",
        )
    if not contents:
        raise HTTPException(status_code=400, detail="上传文件为空")

    # 文件落盘：时间戳前缀 + 原文件名，避免重名覆盖；中文 / 空格保留。
    storage_dir = _ensure_storage_dir()  # 已是绝对路径
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_orig = re.sub(r"[\\/]+", "_", file.filename)
    disk_name = f"{ts}_{safe_orig}"
    disk_path = (storage_dir / disk_name).resolve()  # 落盘也用绝对路径
    try:
        disk_path.write_bytes(contents)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存文件失败: {exc}") from exc

    pkg = AppPackage(
        name=name.strip() or file.filename,
        file_name=file.filename,
        file_path=str(disk_path),
        platform=plat,
        app_package=(app_package or "").strip() or None,
        bundle_id=(bundle_id or "").strip() or None,
        version=(version or "").strip() or None,
        file_size=len(contents),
        project_id=project_id,
        description=(description or "").strip() or None,
    )
    db.session.add(pkg)
    db.session.flush()
    db.session.refresh(pkg)
    return {"status": "success", "data": _serialize(pkg)}


@router.get("")
def list_app_packages(
    db: DBDep,
    platform: Optional[str] = Query(None, description="android / ios，留空 = 不过滤"),
    project_id: Optional[int] = Query(None, description="只看某项目；留空 = 全部"),
):
    """列出 App 包，新到旧。"""
    q = db.session.query(AppPackage)
    if platform:
        q = q.filter(AppPackage.platform == platform.strip().lower())
    if project_id is not None:
        # null 也保留（全局包对所有项目可见）
        from sqlalchemy import or_

        q = q.filter(
            or_(AppPackage.project_id == project_id, AppPackage.project_id.is_(None))
        )
    rows = q.order_by(AppPackage.upload_time.desc(), AppPackage.id.desc()).all()
    return {"status": "success", "data": [_serialize(r) for r in rows]}


@router.get("/{pkg_id}")
def get_app_package(pkg_id: int, db: DBDep):
    pkg = db.session.query(AppPackage).filter(AppPackage.id == pkg_id).first()
    if pkg is None:
        raise HTTPException(status_code=404, detail="安装包不存在")
    return {"status": "success", "data": _serialize(pkg)}


@router.delete("/{pkg_id}")
def delete_app_package(pkg_id: int, db: DBDep):
    pkg = db.session.query(AppPackage).filter(AppPackage.id == pkg_id).first()
    if pkg is None:
        raise HTTPException(status_code=404, detail="安装包不存在")

    # 先尝试删磁盘文件 —— 删不了不阻断（可能被人手挪走 / 权限改了）；DB 记录还得删干净。
    try:
        fp = _abs_path(pkg.file_path)  # 历史相对路径也能解析到
        if fp:
            p = Path(fp)
            if p.exists():
                p.unlink()
    except OSError:
        pass

    db.session.delete(pkg)
    return {"status": "success", "message": "安装包已删除"}


@router.get("/{pkg_id}/download")
def download_app_package(pkg_id: int, db: DBDep):
    """下载原始文件。前端可以拿来给用户做"另存"或"二次确认"。"""
    pkg = db.session.query(AppPackage).filter(AppPackage.id == pkg_id).first()
    if pkg is None:
        raise HTTPException(status_code=404, detail="安装包不存在")
    p = Path(_abs_path(pkg.file_path))  # 兼容历史相对路径
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="文件已不在磁盘上")

    # 文件名按 RFC 5987 给中文留路；media_type 让浏览器下载而不是猜成 zip
    media_type = (
        "application/vnd.android.package-archive"
        if pkg.platform == "android"
        else "application/octet-stream"
    )
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{quote(pkg.file_name)}"; '
            f"filename*=UTF-8''{quote(pkg.file_name)}"
        )
    }
    return FileResponse(path=str(p), media_type=media_type, headers=headers)
