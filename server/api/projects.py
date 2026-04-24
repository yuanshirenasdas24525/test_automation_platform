"""
/api/projects/* 路由。

修了老版里两个坑：
  1. N+1：列表接口改走 `services.projects.list_projects_with_stats`。
  2. `len(None)` 崩溃：`description` 为 None 时不再炸。

返回信封保持老版 `{"status":"success", "data": ...}` 不变，前端零迁移。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import Project, ProjectCreate
from server.services.projects import list_projects_with_stats, serialize_project_basic

router = APIRouter(prefix="/projects", tags=["projects"])

# 项目名 / 描述的长度上限（跟前端 zod schema 保持一致）。
_NAME_MAX = 10
_DESC_MAX = 50


def _validate_project_payload(data: ProjectCreate) -> None:
    if not data.name or not data.type:
        raise HTTPException(status_code=400, detail="名称和类型不能为空")
    if len(data.name) > _NAME_MAX:
        raise HTTPException(status_code=400, detail=f"名称最多 {_NAME_MAX} 个字符")
    # description 是 Optional[str]；None 就跳过长度校验
    if data.description is not None and len(data.description) > _DESC_MAX:
        raise HTTPException(status_code=400, detail=f"描述最多 {_DESC_MAX} 个字符")


@router.get("/list")
def get_projects(
    db: DBDep,
    type_filter: Optional[str] = Query(None, alias="type"),
):
    """列出项目 + 聚合统计。`type` 参数走 ilike，大小写不敏感。"""
    data = list_projects_with_stats(db.session, type_filter=type_filter)
    return {"status": "success", "data": data}


@router.post("")
def create_project(project: ProjectCreate, db: DBDep):
    _validate_project_payload(project)

    db_project = Project(**project.model_dump())
    db.session.add(db_project)
    db.session.flush()  # 拿 id，但最终 commit 交给 get_db
    db.session.refresh(db_project)
    return {"status": "success", "data": serialize_project_basic(db_project)}


@router.put("/{project_id}")
def update_project(project_id: int, project_data: ProjectCreate, db: DBDep):
    _validate_project_payload(project_data)

    db_project = (
        db.session.query(Project).filter(Project.id == project_id).first()
    )
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    for key, value in project_data.model_dump().items():
        setattr(db_project, key, value)

    db.session.flush()
    db.session.refresh(db_project)
    return {"status": "success", "data": serialize_project_basic(db_project)}


@router.get("/{project_id}")
def get_project_info(project_id: int, db: DBDep):
    project = db.session.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"status": "success", "data": serialize_project_basic(project)}


@router.delete("/{project_id}")
def delete_project(project_id: int, db: DBDep):
    db_project = (
        db.session.query(Project).filter(Project.id == project_id).first()
    )
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # modules 关系上挂了 cascade="all, delete-orphan"，关联的模块 / 用例会一起删。
    db.session.delete(db_project)
    return {"status": "success", "message": "项目已删除"}


# ---------------------------------------------------------------------------
# 用例 Excel 导入（历史上和 projects 绑在一起，保留在同一 router 里）
# ---------------------------------------------------------------------------
from fastapi import File, UploadFile  # noqa: E402  放这里减少启动时的顶层依赖

from database.models import TestCase  # noqa: E402


@router.post("/{project_id}/import_cases")
async def import_test_cases(
    project_id: int,
    db: DBDep,
    module_id: int = Query(..., description="导入到哪个子模块下"),
    file: UploadFile = File(...),
):
    """从 Excel 批量导入用例。解析失败会回滚并抛 400。"""
    # 延迟 import：pandas 启动开销大，不放顶层
    import io

    import pandas as pd

    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as exc:  # pandas / xlrd 抛的各种异常都按 400 返回
        raise HTTPException(status_code=400, detail=f"文件解析失败: {exc}") from exc

    import_count = 0
    try:
        for index, row in df.iterrows():
            new_case = TestCase(
                module_id=module_id,
                name=str(row["case_title"]) if pd.notna(row["case_title"]) else "未命名",
                description=row["case_name"],
                method=str(row["method"]).upper(),
                path=str(row["path"]).strip(),
                data_type=row["parametric_type"]
                if pd.notna(row["parametric_type"])
                else "application/json",
                headers=str(row["header"]) if pd.notna(row["header"]) else None,
                params=str(row["data"]) if pd.notna(row["data"]) else None,
                extract_data=str(row["extra"]) if pd.notna(row["extra"]) else None,
                assertion=str(row["expect"]) if pd.notna(row["expect"]) else None,
                sql_query=str(row["sql"]) if pd.notna(row["sql"]) else None,
                skip=str(row["skip"]).lower() == "y" if pd.notna(row["skip"]) else False,
                wait_time=int(row["wait"]) if pd.notna(row["wait"]) else 0,
                sort_order=int(index),
            )
            db.session.add(new_case)
            import_count += 1
    except KeyError as exc:  # 模板列名不对
        raise HTTPException(
            status_code=400, detail=f"Excel 模板缺少列: {exc}"
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"导入失败: {exc}") from exc

    # commit 交给 get_db 兜底。
    return {"status": "success", "message": f"成功导入 {import_count} 条用例"}
