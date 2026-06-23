"""
/api/run_test 触发用例执行；/api/reorder 调整用例 / 模块顺序。

执行接口职责：
  1. 用 v1 或 v2 loader 从 DB 把用例拉出来；
  2. 建一条 "running" 状态的 TestReport；
  3. 扔给 Celery 的 run_test_task 异步跑；
  4. 同步返回 report_id / task_id，前端去轮询。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from server.api.deps import DBDep
from database.models import (
    AUTOMATED_CASE_TYPES,
    CASE_TYPE_FUNCTIONAL,
    ConfigStore,
    Module,
    ReorderRequest,
    RunTestRequest,
    TestCase,
    TestReport,
)
from utils.logger import LOGGER

router = APIRouter(tags=["runs"])


@router.post("/run_test")
async def run_test(req: RunTestRequest, db: DBDep):
    # 延迟 import：read_test_cases 里会拖一堆 loader 依赖；tasks 会拖 Celery。
    from tasks import run_test_task
    from utils.read_test_cases import get_cases_v2_from_db

    if not req.category:
        raise HTTPException(status_code=400, detail="缺少项目类型 api/web/android/ios")

    # functional 用例不走自动化 dispatcher —— 它们由 /api/functional_cases/.../mark
    # 这条人工勾结果路径处理。这里在最早期就拦掉，避免误透传到 Celery / pytest。
    cat_lower = str(req.category or "").strip().lower()
    if cat_lower == CASE_TYPE_FUNCTIONAL:
        raise HTTPException(
            status_code=400,
            detail="功能用例（functional）不支持自动化执行，请走 /api/functional_cases/{id}/mark 勾结果",
        )

    now = datetime.now()
    task_id = f"{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

    params = {
        "project": req.project,
        "module": req.module,
        "category": req.category,
        "case": req.case,
    }
    LOGGER.info(f"run_test params={params}")

    # v2 唯一 loader：带 steps / environment / case_type 过滤。
    # v1 raw SQL 路径已删；没 steps 的老用例需先跑数据迁移
    # （database/migrations/data_migrations/v2_cases_to_steps.py）。
    cat = cat_lower
    try:
        if req.case_ids:
            cases_to_run = []
            seen_ids: set[int] = set()
            for case_id in req.case_ids:
                if case_id in seen_ids:
                    continue
                seen_ids.add(case_id)
                cases_to_run.extend(get_cases_v2_from_db(
                    {**params, "module": None, "case": case_id},
                    db.session,
                ))
        else:
            cases_to_run = get_cases_v2_from_db(params, db.session)
    except Exception as exc:
        # Loader 自己的异常按 400 吐：上游多半是参数错（project/module/case 不存在等）
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 防御性过滤（loader 已经在 ORM 里过滤过一次，这里 belt-and-suspenders）：
    #   - functional 用例不能走自动化（没 steps，dispatcher 跑不动）→ 永远剔
    #   - 用户点"运行 X 全部" → 只放 case_type IN {X, mixed}
    #   - 用户单跑指定 case_id → 不做 category 过滤（已经明确到具体用例）
    # v1 兼容（NULL case_type 视作 api）已删 —— case_type 必须明确赋值。
    _RUN_CATS_AUTO = ("api", "web", "android", "ios")
    allowed: set[str] = {cat, "mixed"} if cat in _RUN_CATS_AUTO else set()
    def _is_runnable(c) -> bool:
        raw = c.get("case_type") if isinstance(c, dict) else getattr(c, "case_type", None)
        ct = raw or ""
        if ct == CASE_TYPE_FUNCTIONAL:
            return False
        if not allowed or req.case is not None or bool(req.case_ids):
            return ct in AUTOMATED_CASE_TYPES
        return ct in allowed
    cases_to_run = [c for c in cases_to_run if _is_runnable(c)]

    if not cases_to_run:
        raise HTTPException(status_code=404, detail="未找到可执行的用例")

    # ---- 指定设备：前端 RunCaseDialog 让用户在 app/android/ios 用例上手选某台设备 ----
    # 逻辑：
    #   - 必须是 idle。busy/offline 都拒绝（409）——否则要么抢别人的任务，要么根本连不上。
    #   - 只对 category ∈ {app, android, ios} 有效；其它场景（api/web）直接忽略
    #     传错参数也不报错。
    #   - 把 device_id 注入到每条 case_dict 上，透传到 CaseExecutor / acquire_session_for_case。
    _APP_LIKE_CATS = ("android", "ios")
    if req.device_id is not None and cat in _APP_LIKE_CATS:
        from database.models import Device, DEVICE_STATUS_IDLE

        dev = db.session.query(Device).filter(Device.id == req.device_id).first()
        if dev is None:
            raise HTTPException(status_code=404, detail=f"device_id={req.device_id} 不存在")
        if dev.status != DEVICE_STATUS_IDLE:
            raise HTTPException(
                status_code=409,
                detail=f"设备 {dev.udid} 当前 status={dev.status}，不是 idle，不能选作运行设备",
            )
        # 注入：CaseExecutor.acquire_session_for_case 会读 case_dict["device_id"]
        for c in cases_to_run:
            if isinstance(c, dict):
                c["device_id"] = req.device_id
        LOGGER.info(
            f"[run_test] 指定设备: id={dev.id} udid={dev.udid} (强制 acquire_by_id)"
        )

    case_number = len(cases_to_run)

    # 状态设为 running，这样前端可以显示"进行中"
    new_report = TestReport(
        project_id=req.project,
        category=req.category,
        status="running",
        start_time=now,
        executor="System",
        total_count=case_number,
    )
    db.session.add(new_report)
    db.session.flush()
    db.session.refresh(new_report)
    report_id = new_report.id

    # Celery 异步触发（这里的 commit 由 get_db 兜底，确保 report 记录对 worker 可见）
    db.commit()
    LOGGER.info(f"看看cases_to_run是啥：{cases_to_run}")

    # 同步模式：读取配置中心 web.browser.sync_mode，
    # 开启后任务直接在 uvicorn 进程内同步执行，浏览器弹窗可见
    sync_mode = False
    if cat_lower == "web":
        sync_row = (
            db.session.query(ConfigStore)
            .filter(
                ConfigStore.category == "web",
                ConfigStore.config_group == "browser",
                ConfigStore.config_key == "sync_mode",
            )
            .first()
        )
        if sync_row and sync_row.config_value:
            val = sync_row.config_value.strip().lower()
            sync_mode = val in ("1", "true", "yes", "on")

    if sync_mode:
        LOGGER.info("[sync_mode] 同步执行 Web 用例（apply），浏览器将弹出当前桌面")
        run_test_task.apply(args=(task_id, report_id, cases_to_run, req.category))
    else:
        run_test_task.delay(task_id, report_id, cases_to_run, req.category)

    return {
        "status": "success",
        "report_id": report_id,
        "task_id": task_id,
        "case_number": case_number,
        "message": "测试已在后台启动" if not sync_mode else "测试已在当前进程同步执行",
    }


@router.patch("/reorder")
def reorder_items(req: ReorderRequest, db: DBDep):
    """前端拖拽排序后批量更新 sort_order。"""
    for item in req.items:
        target = Module if item.type == "module" else TestCase
        db.session.query(target).filter(target.id == item.id).update(
            {"sort_order": item.new_order},
            synchronize_session=False,
        )
    return {"status": "success"}
