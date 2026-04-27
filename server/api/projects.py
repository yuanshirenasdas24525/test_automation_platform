"""
/api/projects/* 路由。

修了老版里两个坑：
  1. N+1：列表接口改走 `services.projects.list_projects_with_stats`。
  2. `len(None)` 崩溃：`description` 为 None 时不再炸。

返回信封保持老版 `{"status":"success", "data": ...}` 不变，前端零迁移。
"""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from server.api.deps import DBDep
from database.models import (
    ALL_CASE_TYPES,
    ALL_PROJECT_STACKS,
    Module,
    Project,
    ProjectCreate,
    TestCase,
)
from server.services.projects import list_projects_with_stats, serialize_project_basic

router = APIRouter(prefix="/projects", tags=["projects"])

# 项目名 / 描述的长度上限（跟前端 zod schema 保持一致）。
_NAME_MAX = 10
_DESC_MAX = 50


def _validate_project_payload(data: ProjectCreate) -> None:
    """
    名称 / 长度的浅校验。

    `enabled_stacks` 的非空 + 枚举值合法已经由 ProjectCreate 的 pydantic
    field_validator 兜住了（不合法时 422 直接返回），这里不再重复检查，
    只关心名称必填 + 名称 / 描述长度。
    """
    if not data.name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if len(data.name) > _NAME_MAX:
        raise HTTPException(status_code=400, detail=f"名称最多 {_NAME_MAX} 个字符")
    # description 是 Optional[str]；None 就跳过长度校验
    if data.description is not None and len(data.description) > _DESC_MAX:
        raise HTTPException(status_code=400, detail=f"描述最多 {_DESC_MAX} 个字符")


@router.get("/list")
def get_projects(
    db: DBDep,
    stack_filter: Optional[str] = Query(
        None,
        alias="stack",
        description="按启用栈过滤：传 api/web/app/functional 返回包含该栈的项目；不传返回全部",
    ),
):
    """列出项目 + 聚合统计。

    v2 起 `type` 参数被 `stack` 替代（语义：项目 enabled_stacks 是否包含该栈）。
    旧的单值 `type=api` 行为不再支持；前端已同步切换到 `stack=` 调用。
    """
    data = list_projects_with_stats(db.session, stack_filter=stack_filter)
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


# ---------------------------------------------------------------------------
# 栈维度的用例计数（项目详情页栈 Tab 上的角标用：API (12) / Web (5) / ...）
#
# 设计：
#   - 一次 group_by 拿全部 case_type 的计数，避免 4 次单独查；
#   - 哪怕项目没启用某个栈，仍把它的计数返出来（=0）—— 前端可能要展示
#     "禁用 Tab 灰色化但还是显示计数"；具体灰不灰由前端决定，后端就负责数。
#   - mixed 算到第一步骤所属栈里（前端处理），后端不在这里做归并。
#   - functional 用例没 step，但 case_type='functional' 直接归到 functional Tab。
# ---------------------------------------------------------------------------
@router.get("/{project_id}/stack_counts")
def get_stack_counts(project_id: int, db: DBDep):
    """返回 {api, web, app, functional, mixed, total} 形态的用例计数。

    - 只数当前项目下所有模块的用例（不管 enabled_stacks 是否包含该栈，
      因为历史项目可能存在"已禁用栈但还有遗留用例"的情况，前端要能看见）。
    - 返回的 enabled_stacks 让前端不用再单独 GET project 一次。
    """
    from sqlalchemy import func

    project = db.session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 用例计数：跨模块 join，按 case_type 分组
    rows = (
        db.session.query(TestCase.case_type, func.count(TestCase.id))
        .join(Module, TestCase.module_id == Module.id)
        .filter(Module.project_id == project_id)
        .group_by(TestCase.case_type)
        .all()
    )
    counts: dict[str, int] = {ct: 0 for ct in ALL_CASE_TYPES}
    total = 0
    for ct, n in rows:
        key = (ct or "api").lower()
        counts[key] = counts.get(key, 0) + int(n or 0)
        total += int(n or 0)

    return {
        "status": "success",
        "data": {
            "project_id": project_id,
            "enabled_stacks": list(project.enabled_stacks or []),
            "counts": counts,         # {api: 12, web: 5, app: 0, functional: 8, mixed: 1}
            "total": total,
        },
    }


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


# ---------------------------------------------------------------------------
# 用例导出（excel / csv）
#
# 设计要点：
#   - 列名严格对齐 import_test_cases 用的列（case_title / case_name / method /
#     path / parametric_type / header / data / extra / expect / sql / skip /
#     wait），导出 → 改 → 重新导入 是个完整闭环；
#   - 支持 module_id 过滤：null 即整个项目，指定值则只导出该模块**及其子模块**；
#   - format=xlsx 走 pandas → openpyxl，format=csv 直接 to_csv（utf-8-sig 带 BOM，
#     Excel 双击不乱码）；
#   - StreamingResponse 把 BytesIO 直接交给客户端，不在 disk 落地（用例量级
#     一般 <几千条，内存压力可接受）；
#   - Content-Disposition 文件名按"项目名_时间戳.{ext}"拼，保证用户多次导出不覆盖。
# ---------------------------------------------------------------------------
def _collect_module_ids(db, project_id: int, root_module_id: Optional[int]) -> Optional[list[int]]:
    """收集要导出的模块 id 列表。

    - root_module_id is None：返回 None，调用方按"整个项目"处理（只过滤 project_id）；
    - 否则做 BFS，把 root + 所有后代 module 的 id 收齐返回。
    """
    if root_module_id is None:
        return None

    from database.models import Module  # 延迟 import，避免顶层循环

    # 校验 root 模块属于当前 project，避免越权
    root = (
        db.session.query(Module)
        .filter(Module.id == root_module_id, Module.project_id == project_id)
        .first()
    )
    if root is None:
        raise HTTPException(status_code=404, detail=f"模块 {root_module_id} 不在项目 {project_id} 下")

    collected = [root.id]
    frontier = [root.id]
    while frontier:
        children = (
            db.session.query(Module.id)
            .filter(Module.parent_id.in_(frontier))
            .all()
        )
        next_frontier = [r[0] for r in children]
        if not next_frontier:
            break
        collected.extend(next_frontier)
        frontier = next_frontier
    return collected


def _parse_case_types_required(raw: Optional[str]) -> set[str]:
    """把 ?case_type=api,mixed 拆成集合；空 / 不传 / 全非法值 → 422。

    与 server.api.content._parse_case_types 不同：这里"必须"有合法值，
    因为 export 是用户主动按栈过滤的操作，缺 case_type 没有合理默认。
    """
    if not raw:
        raise HTTPException(
            status_code=422,
            detail="case_type 必填（api/web/android/ios/app/mixed/functional，多值用逗号分隔）",
        )
    wanted = {t.strip().lower() for t in raw.split(",") if t.strip()}
    valid = wanted & ALL_CASE_TYPES
    if not valid:
        raise HTTPException(
            status_code=422,
            detail=f"case_type 没有合法值：{sorted(wanted)}，可选 {sorted(ALL_CASE_TYPES)}",
        )
    return valid


@router.get("/{project_id}/export_cases")
def export_test_cases(
    project_id: int,
    db: DBDep,
    module_id: int = Query(
        ..., description="导出范围：必填，从该模块（含所有后代模块）按树前序导出"
    ),
    case_type: str = Query(
        ...,
        description="按栈过滤用例：必填，多值逗号分隔。如 api,mixed / web,mixed / functional",
    ),
    format: str = Query("xlsx", pattern="^(xlsx|csv)$", description="xlsx 或 csv"),
):
    """导出用例为 Excel / CSV。

    v2 行为（2026-04 改造）：
      - module_id 必填：根目录不导（前端也已经隐藏了"导出"按钮）；
      - case_type 必填：跟项目详情页当前栈 Tab 一致（API/Web/App 通常是
        `xxx,mixed`，Functional 是 `functional`）；
      - 输出顺序：从 module_id 这棵子树做前序遍历，每层 modules + cases
        按 sort_order 交错——直接对应用户在 UI 看到的视觉顺序。

    v2 列布局：
      - 公共列：module_name / case_title / case_name / case_type / tags / priority / skip / wait
      - v1 API 列（只对 api/NULL/mixed 用例有意义）：method / path / parametric_type
        / header / data / extra / expect / sql
      - v2 列：steps_json —— web/app/mixed 用例 dump 完整 steps；functional dump functional_spec
      导入侧暂未跟进 steps_json 回灌（TODO），只保证导出真实反映 DB。
    """
    import datetime
    import json

    import pandas as pd

    project = db.session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    types_filter = _parse_case_types_required(case_type)

    # 把"以 module_id 为根的子树"按 (parent_id -> 子模块列表) 索引一次，
    # 同时把"该子树涉及的所有 case"也一次拉回来，避免后面递归走 N+1。
    from sqlalchemy.orm import selectinload
    from database.models import Module, TestCase  # noqa: E402

    module_ids = _collect_module_ids(db, project_id, module_id)
    if module_ids is None:
        # 防御性：当前签名 module_id 必填，不会走到这里；保留兜底以防日后改签名
        module_ids = []

    # 用 sort_order 升序拿同层 modules（注意：需要保留属于子树范围的 modules，
    # 包括 root 自己，便于 walk() 时按 root 的子节点列表展开第一层）
    all_modules = (
        db.session.query(Module)
        .filter(Module.project_id == project_id, Module.id.in_(module_ids))
        .order_by(Module.parent_id, Module.sort_order.asc(), Module.id.asc())
        .all()
    )
    children_by_parent: dict[Optional[int], list[Module]] = {}
    for m in all_modules:
        children_by_parent.setdefault(m.parent_id, []).append(m)

    # 同样按子树拉 cases；这里就把 case_type 过滤掉，少 select、少跨过滤逻辑
    cases_q = (
        db.session.query(TestCase, Module.name.label("module_name"))
        .join(Module, TestCase.module_id == Module.id)
        .filter(
            Module.project_id == project_id,
            TestCase.module_id.in_(module_ids),
            TestCase.case_type.in_(types_filter),
        )
        .options(selectinload(TestCase.steps))
        .order_by(TestCase.module_id, TestCase.sort_order.asc(), TestCase.id.asc())
        .all()
    )
    cases_by_module: dict[int, list[tuple]] = {}
    for case, module_name in cases_q:
        cases_by_module.setdefault(case.module_id, []).append((case, module_name))

    # 前序遍历：当前模块的 cases + submodules 按 sort_order 交错，submodule
    # 命中时整棵子树展开后再继续。视觉上跟前端文件管理器看到的顺序一致。
    def walk(mid: int):
        # 同层混排 (sort_order, kind, payload)
        items: list[tuple[int, str, object]] = []
        for c, mn in cases_by_module.get(mid, []):
            items.append((int(c.sort_order or 0), "case", (c, mn)))
        for m in children_by_parent.get(mid, []):
            items.append((int(m.sort_order or 0), "module", m))
        # 同 sort_order 时优先 case 再 module（保证视觉稳定，跟前端 sorted 一致）
        items.sort(key=lambda t: (t[0], 0 if t[1] == "case" else 1))
        for _, kind, payload in items:
            if kind == "case":
                yield payload  # (case, module_name)
            else:
                yield from walk(payload.id)  # 递归子模块

    rows = list(walk(module_id))

    # 构造 DataFrame：列名严格对应 import_test_cases 解析时的 row[...] key
    def _serialize_steps_for_export(case) -> str:
        """v2 case：把 steps 列表 dump 成紧凑 JSON 字符串。functional：dump
        functional_spec。api / NULL：留空（v1 字段已经在前面那批列里了）。
        """
        ct = (case.case_type or "api").lower()
        if ct == "functional":
            spec = getattr(case, "functional_spec", None)
            return json.dumps(spec, ensure_ascii=False) if spec else ""
        if ct in ("web", "android", "ios", "mixed"):
            steps = sorted(case.steps or [], key=lambda s: s.step_order or 0)
            payload = [
                {
                    "step_order": s.step_order,
                    "step_name": s.step_name,
                    "step_type": s.step_type,
                    "skip": bool(s.skip),
                    "config": s.config,
                    "extract": s.extract,
                    "assertion": s.assertion,
                    "wait_before": s.wait_before,
                    "timeout": s.timeout,
                    "retry": s.retry,
                    "on_failure": s.on_failure,
                }
                for s in steps
            ]
            return json.dumps(payload, ensure_ascii=False)
        return ""

    records = []
    for case, module_name in rows:
        records.append(
            {
                "module_name": module_name or "",            # 仅参考列，导入忽略
                "case_title": case.name or "",
                "case_name": case.description or "",
                # v2 公共列
                "case_type": case.case_type or "api",
                "tags": ",".join(case.tags or []) if isinstance(case.tags, list) else "",
                "priority": case.priority if case.priority is not None else "",
                # v1 API 列（web/app/functional 这些通常为空）
                "method": case.method or "",
                "path": case.path or "",
                "parametric_type": case.data_type or "",
                "header": case.headers or "",
                "data": case.params or "",
                "extra": case.extract_data or "",
                "expect": case.assertion or "",
                "sql": case.sql_query or "",
                "skip": "y" if case.skip else "n",
                "wait": int(case.wait_time or 0),
                # v2/functional 步骤集中放这一列（紧凑 JSON），导入侧 TODO 回灌
                "steps_json": _serialize_steps_for_export(case),
            }
        )
    df = pd.DataFrame(records)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_project_name = (project.name or f"project_{project_id}").replace("/", "_").replace("\\", "_")
    base_name = f"{safe_project_name}_cases_{ts}"

    buf = io.BytesIO()
    if format == "xlsx":
        # openpyxl 是默认 engine，pandas 装上即可；不强制 sheet name 简化
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="cases")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{base_name}.xlsx"
    else:  # csv
        # utf-8-sig 带 BOM：Excel 双击 csv 不会乱码
        text = df.to_csv(index=False)
        buf.write(text.encode("utf-8-sig"))
        media = "text/csv; charset=utf-8"
        filename = f"{base_name}.csv"
    buf.seek(0)

    # Content-Disposition 文件名要走 RFC 5987 才能安全携带中文项目名；
    # 老一些的浏览器看 filename，新的看 filename*；同时给两个最稳。
    from urllib.parse import quote
    disposition = (
        f'attachment; filename="{quote(filename)}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    headers = {"Content-Disposition": disposition}
    return StreamingResponse(buf, media_type=media, headers=headers)
