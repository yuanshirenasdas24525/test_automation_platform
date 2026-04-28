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
    """从 Excel 批量导入用例（per-step 一行格式，对应新 export 格式）。

    Sheet 识别策略：
      - Excel 含"功能用例"sheet → functional 用例导入路径
      - 含"用例"sheet → 自动化用例导入路径（按"用例#"分组重建 case + steps）
      - 都没有 → 取第一个 sheet 兜底（CSV / 老格式继续可用）

    导入失败会回滚整批并抛 400。
    """
    # 延迟 import：pandas 启动开销大，不放顶层
    import io
    import json as _json

    import pandas as pd
    from database.models import TestStep

    contents = await file.read()
    try:
        # 一次性把所有 sheet 都读出来，按名字分发
        sheets = pd.read_excel(io.BytesIO(contents), sheet_name=None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {exc}") from exc

    # 取主 sheet：优先 "用例"，没有再"功能用例"，都没有取第一个
    if "用例" in sheets:
        df = sheets["用例"]
        is_functional = False
    elif "功能用例" in sheets:
        df = sheets["功能用例"]
        is_functional = True
    else:
        # 兼容只有一个 sheet 的 CSV / 老 export
        first_name = next(iter(sheets), None)
        if not first_name:
            raise HTTPException(status_code=400, detail="文件里没有 sheet")
        df = sheets[first_name]
        # 通过列名启发式：含"前置条件"/"预期结果"就当 functional
        is_functional = "前置条件" in df.columns or "预期结果" in df.columns

    def _truthy_skip(v) -> bool:
        if pd.isna(v):
            return False
        s = str(v).strip().lower()
        return s in ("y", "yes", "true", "1", "是")

    def _safe_str(v) -> str:
        return "" if pd.isna(v) else str(v)

    def _safe_json(v):
        """单元格里的 JSON 列：空 → None；解析失败 → 报错让用户排查。"""
        if pd.isna(v) or str(v).strip() == "":
            return None
        try:
            return _json.loads(str(v))
        except Exception as exc:
            raise ValueError(f"JSON 解析失败：{v!r} ({exc})")

    import_count = 0

    try:
        if is_functional:
            # ───────── functional 用例：一行一条 ─────────
            for index, row in df.iterrows():
                title = _safe_str(row.get("用例标题")).strip()
                if not title:
                    continue  # 跳过空行

                preconditions_raw = _safe_str(row.get("前置条件"))
                steps_raw = _safe_str(row.get("步骤"))
                expected = _safe_str(row.get("预期结果")) or None

                # 单元格内 \n 分隔多行
                preconditions = [
                    line.strip()
                    for line in preconditions_raw.split("\n")
                    if line.strip()
                ]
                steps_list = [
                    line.strip() for line in steps_raw.split("\n") if line.strip()
                ]

                tags_raw = _safe_str(row.get("标签"))
                tags = (
                    [t.strip() for t in tags_raw.split(",") if t.strip()]
                    if tags_raw
                    else None
                )
                priority = (
                    int(row["优先级"]) if pd.notna(row.get("优先级")) else None
                )

                new_case = TestCase(
                    module_id=module_id,
                    name=title,
                    description=_safe_str(row.get("描述")) or None,
                    case_type="functional",
                    skip=_truthy_skip(row.get("跳过")),
                    priority=priority,
                    tags=tags,
                    sort_order=int(index),
                    functional_spec={
                        "preconditions": preconditions,
                        "steps": steps_list,
                        "expected": expected,
                    },
                )
                db.session.add(new_case)
                import_count += 1
        else:
            # ───────── 自动化用例：per-step 一行，按"用例#"分组 ─────────
            # 列存在性兜底（用户可能改过列名）
            REQUIRED = ["用例#"]
            missing = [c for c in REQUIRED if c not in df.columns]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Excel 缺少必要列：{missing}（建议用最新版导出模板编辑）",
                )

            # 按"用例#"分组（NaN 忽略）
            groups: dict = {}
            order: list = []
            for index, row in df.iterrows():
                no = row.get("用例#")
                if pd.isna(no):
                    continue
                key = int(no)
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append((int(index), row))

            for case_no in order:
                rows = groups[case_no]
                first_idx, first_row = rows[0]

                title = _safe_str(first_row.get("用例标题")).strip()
                if not title:
                    raise HTTPException(
                        status_code=400,
                        detail=f"用例#{case_no} 第一行缺"
                               f"\"用例标题\"（每个用例第一行必须有标题）",
                    )

                tags_raw = _safe_str(first_row.get("标签"))
                tags = (
                    [t.strip() for t in tags_raw.split(",") if t.strip()]
                    if tags_raw
                    else None
                )
                priority = (
                    int(first_row["优先级"])
                    if pd.notna(first_row.get("优先级"))
                    else None
                )
                case_type = (
                    _safe_str(first_row.get("用例类型")).strip().lower() or "api"
                )

                new_case = TestCase(
                    module_id=module_id,
                    name=title,
                    description=_safe_str(first_row.get("描述")) or None,
                    case_type=case_type,
                    skip=_truthy_skip(first_row.get("跳过")),
                    priority=priority,
                    tags=tags,
                    sort_order=int(first_idx),
                )
                db.session.add(new_case)
                db.session.flush()  # 拿 id

                # 把 rows 按"步骤序号"升序排（容错：用户可能没填）
                def _step_sort_key(r):
                    _, rr = r
                    v = rr.get("步骤序号")
                    return int(v) if pd.notna(v) else 0

                step_rows = sorted(rows, key=_step_sort_key)

                for step_pos, (_, sr) in enumerate(step_rows):
                    step_type = _safe_str(sr.get("步骤类型")).strip()
                    if not step_type:
                        # 该用例没有 step 行（功能用例 / 单 case 没填步骤）
                        continue

                    # 重组 config 字典
                    config: dict = {}
                    for col_key, cfg_key in [
                        ("定位方式", "by"),
                        ("定位表达式", "locator"),
                        ("输入值", "value"),
                        ("方法", "method"),
                        ("数据类型", "data_type"),
                        ("SQL", "sql_query"),
                    ]:
                        v = sr.get(col_key)
                        if pd.notna(v) and str(v).strip():
                            config[cfg_key] = str(v)

                    # URL/路径 → http_request 类用 path（兼容 v1）；其余用 url
                    url_or_path = _safe_str(sr.get("URL/路径")).strip()
                    if url_or_path:
                        if step_type == "http_request":
                            config["path"] = url_or_path
                        else:
                            config["url"] = url_or_path

                    # 请求头 / 请求体 是 JSON
                    headers = _safe_json(sr.get("请求头"))
                    if headers is not None:
                        config["headers"] = headers
                    params = _safe_json(sr.get("请求体"))
                    if params is not None:
                        config["params"] = params

                    # 应用包：单纯字符串，按 step_type 决定塞哪个 key
                    app_pkg = _safe_str(sr.get("应用包")).strip()
                    if app_pkg:
                        if step_type == "app_install":
                            config["app_path"] = app_pkg
                        elif "ios" in (case_type, ""):
                            # 简化：iOS 类用例 → bundleId；否则 appPackage
                            config["bundleId"] = app_pkg
                        else:
                            config["appPackage"] = app_pkg

                    # 其它配置（JSON）—— 跟显式列合并，显式列优先
                    others = _safe_json(sr.get("其它配置")) or {}
                    if isinstance(others, dict):
                        for k, v in others.items():
                            config.setdefault(k, v)

                    extract = _safe_json(sr.get("提取规则"))
                    assertion = _safe_json(sr.get("断言规则"))

                    db.session.add(TestStep(
                        case_id=new_case.id,
                        step_order=step_pos,
                        step_name=_safe_str(sr.get("步骤名")).strip() or step_type,
                        step_type=step_type,
                        skip=_truthy_skip(sr.get("跳过此步")),
                        config=config,
                        extract=extract,
                        assertion=assertion,
                        wait_before=int(sr["等待ms"]) if pd.notna(sr.get("等待ms")) else 0,
                        timeout=int(sr["超时s"]) if pd.notna(sr.get("超时s")) else 30,
                        retry=int(sr["重试"]) if pd.notna(sr.get("重试")) else 0,
                        on_failure=_safe_str(sr.get("失败策略")).strip() or "stop",
                    ))

                import_count += 1
    except HTTPException:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        raise HTTPException(status_code=400, detail=f"导入失败: {exc}") from exc

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

    # ─────────────────────────────────────────────────────────────────────
    # 新格式（2026-04 重构）：每个 step 一行，case meta 只在该 case 第一行。
    #
    # 规则：
    #   - 一个 N-step 用例 → N 行，第一行带"用例标题/描述/类型/标签/优先级/跳过/模块"等
    #     case 元信息；后续 step 行的这些列**留空**；
    #   - "用例#" 列贯穿所有行，导入时按这一列分组；
    #   - 列名走业务语义（定位方式 / 输入值 / 请求头等），不再是 v1 的
    #     method/path 单层 dump；
    #   - "其它配置" 列吃掉剩下的 step.config 字段（少见的字段不暴露成列，避免列爆炸）；
    #   - extract / assertion 仍是 JSON list（结构化太复杂，不展平）；
    #   - functional 用例不走 step：单独放 sheet "功能用例"，preconditions / steps /
    #     expected 用单元格内 \n 分隔。
    # ─────────────────────────────────────────────────────────────────────

    # 各 step.config 的"显式列"键集合（这些键单独展出来，剩下的进 "其它配置"）
    _EXPLICIT_CONFIG_KEYS = {
        "by", "locator", "value",                        # 通用定位
        "method", "url", "path",                         # http_request
        "headers", "params", "data_type", "sql_query",   # http_request
        "appPackage", "bundleId", "appActivity", "app_path",  # app
    }

    def _split_config(cfg: dict) -> tuple[dict, dict]:
        """把 step.config 拆成 (显式字段 dict, 其它字段 dict)。"""
        if not isinstance(cfg, dict):
            return {}, {}
        explicit = {k: v for k, v in cfg.items() if k in _EXPLICIT_CONFIG_KEYS}
        others = {k: v for k, v in cfg.items() if k not in _EXPLICIT_CONFIG_KEYS}
        return explicit, others

    def _meta_row(case, module_name: str) -> dict:
        """case 元信息列（仅每个 case 的第一行填）。"""
        return {
            "用例标题": case.name or "",
            "模块路径": module_name or "",
            "描述": case.description or "",
            "用例类型": case.case_type or "",
            "标签": ",".join(case.tags or []) if isinstance(case.tags, list) else "",
            "优先级": case.priority if case.priority is not None else "",
            "跳过": "y" if case.skip else "n",
        }

    _BLANK_META = {k: "" for k in _meta_row(TestCase(name="", case_type="api"), "")}

    def _step_row(step) -> dict:
        """每个 step 一行的"步骤"列。"""
        cfg = step.config or {}
        explicit, others = _split_config(cfg)
        # url 字段：http_request 用 path/url 都可能；统一展示成 URL/路径
        url_or_path = explicit.get("url") or explicit.get("path") or ""
        # appPackage / bundleId / app_path 三选一展示成"应用包"
        app_pkg = (
            explicit.get("appPackage")
            or explicit.get("bundleId")
            or explicit.get("app_path")
            or ""
        )
        return {
            "步骤序号": step.step_order if step.step_order is not None else "",
            "步骤名": step.step_name or "",
            "步骤类型": step.step_type or "",
            "跳过此步": "y" if step.skip else "n",
            "定位方式": explicit.get("by") or "",
            "定位表达式": explicit.get("locator") or "",
            "输入值": explicit.get("value") or "",
            "方法": explicit.get("method") or "",
            "URL/路径": url_or_path,
            "请求头": json.dumps(explicit.get("headers"), ensure_ascii=False)
                       if explicit.get("headers") else "",
            "请求体": json.dumps(explicit.get("params"), ensure_ascii=False)
                       if explicit.get("params") else "",
            "数据类型": explicit.get("data_type") or "",
            "SQL": explicit.get("sql_query") or "",
            "应用包": app_pkg,
            "其它配置": json.dumps(others, ensure_ascii=False) if others else "",
            "提取规则": json.dumps(step.extract, ensure_ascii=False) if step.extract else "",
            "断言规则": json.dumps(step.assertion, ensure_ascii=False) if step.assertion else "",
            "等待ms": step.wait_before if step.wait_before is not None else 0,
            "超时s": step.timeout if step.timeout is not None else 30,
            "重试": step.retry if step.retry is not None else 0,
            "失败策略": step.on_failure or "stop",
        }

    _BLANK_STEP = {k: "" for k in _step_row(__import__("types").SimpleNamespace(
        step_order=None, step_name="", step_type="", skip=False, config={},
        extract=None, assertion=None, wait_before=None, timeout=None, retry=None, on_failure="",
    ))}

    # 决定 sheet 类型：functional 单独走简化布局
    is_functional_export = types_filter == {"functional"}

    case_records: list[dict] = []
    functional_records: list[dict] = []
    case_no = 0
    for case, module_name in rows:
        case_no += 1
        ct = (case.case_type or "").lower()

        if ct == "functional":
            spec = case.functional_spec or {}
            functional_records.append({
                "用例#": case_no,
                "用例标题": case.name or "",
                "模块路径": module_name or "",
                "描述": case.description or "",
                "标签": ",".join(case.tags or []) if isinstance(case.tags, list) else "",
                "优先级": case.priority if case.priority is not None else "",
                "跳过": "y" if case.skip else "n",
                "前置条件": "\n".join(spec.get("preconditions") or []),
                "步骤": "\n".join(spec.get("steps") or []),
                "预期结果": spec.get("expected") or "",
            })
            continue

        steps = sorted(case.steps or [], key=lambda s: s.step_order or 0)
        meta = _meta_row(case, module_name)

        if not steps:
            # 无 step 的 case：单行只放 meta，步骤区留空
            row = {"用例#": case_no, **meta, **_BLANK_STEP}
            case_records.append(row)
            continue

        for i, s in enumerate(steps):
            row = {
                "用例#": case_no,
                # case meta 只在第一行填
                **(meta if i == 0 else _BLANK_META),
                **_step_row(s),
            }
            case_records.append(row)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_project_name = (project.name or f"project_{project_id}").replace("/", "_").replace("\\", "_")
    base_name = f"{safe_project_name}_cases_{ts}"

    buf = io.BytesIO()
    if format == "xlsx":
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            if is_functional_export:
                df = pd.DataFrame(functional_records)
                df.to_excel(writer, index=False, sheet_name="功能用例")
            else:
                df = pd.DataFrame(case_records)
                df.to_excel(writer, index=False, sheet_name="用例")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{base_name}.xlsx"
    else:  # csv —— 多 sheet 不适合 csv；统一只导主 sheet
        if is_functional_export:
            df = pd.DataFrame(functional_records)
        else:
            df = pd.DataFrame(case_records)
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
