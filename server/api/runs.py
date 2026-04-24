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
from database.models import Module, ReorderRequest, RunTestRequest, TestCase, TestReport
from utils.logger import LOGGER

router = APIRouter(tags=["runs"])


@router.post("/run_test")
async def run_test(req: RunTestRequest, db: DBDep):
    # 延迟 import：read_test_cases 里会拖一堆 loader 依赖；tasks 会拖 Celery。
    from tasks import run_test_task
    from utils.read_test_cases import get_cases_from_db, get_cases_v2_from_db

    if not req.category:
        raise HTTPException(status_code=400, detail="缺少项目类型 api/web/app")

    now = datetime.now()
    task_id = f"{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

    params = {
        "project": req.project,
        "module": req.module,
        "category": req.category,
        "case": req.case,
    }
    LOGGER.info(f"看看params是啥：{params},----{req}")

    # 选 loader：
    #   - req.v2=True                     → v2 loader（带 steps / environment）
    #   - category != 'api'（web/app 等）  → **强制** v2，因为 v1 loader 用 raw SQL
    #                                        只读 api 字段（method/path/headers…），
    #                                        web/app 用例的 steps 完全拿不到，最后
    #                                        给 pytest 的 case 字典形状不对（缺 steps）。
    #   - 其它（api + 没开 v2）            → v1 raw SQL，保留老行为
    cat = str(req.category or "").strip().lower()
    use_v2 = bool(req.v2) or cat != "api"
    try:
        if use_v2:
            cases_to_run = get_cases_v2_from_db(params, db.session)
        else:
            cases_to_run = get_cases_from_db(params, db.sql)
    except Exception as exc:
        # Loader 自己的异常按 400 吐：上游多半是参数错（project/module/case 不存在等）
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not cases_to_run:
        raise HTTPException(status_code=404, detail="未找到可执行的用例")

    # ---- 指定设备：前端 RunCaseDialog 让用户在 app 用例上手选某台设备 ----
    # 逻辑：
    #   - 必须是 idle。busy/offline 都拒绝（409）——否则要么抢别人的任务，要么根本连不上。
    #   - 只对 category='app' 有效；非 app 场景（api/web）直接忽略，避免传错参数也不报错。
    #   - 把 device_id 注入到每条 case_dict 上，透传到 CaseExecutor / acquire_session_for_case。
    if req.device_id is not None and cat == "app":
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
    run_test_task.delay(task_id, report_id, cases_to_run, req.category)

    return {
        "status": "success",
        "report_id": report_id,
        "task_id": task_id,
        "case_number": case_number,
        "message": "测试已在后台启动",
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
