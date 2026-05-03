"""Celery 应用工厂。

关键特性：
  - 默认：异步模式，需要单独起 `celery -A celery_app worker --loglevel=INFO`
  - 把环境变量 `CELERY_TASK_ALWAYS_EAGER=1`（或 "true"/"yes"）打开后，
    `task.delay(...)` 会直接在当前进程同步执行，不经 Redis，不需要 worker。
    这对本地自测 & 排查 "为什么 run_test 提交后库里没数据" 非常有用——
    把 EAGER 打开，整个流程就在 FastAPI 的 uvicorn 进程里跑完，
    [run_test_task] / [pytest_hook] / [sync_allure_to_db] / [finalize_report]
    所有 print 都会落在 uvicorn 那个终端的 stdout 里。

排查步骤：
  1. 先用 EAGER 跑一次，确认"整条链路本身能写库"；
  2. 能写就说明问题是 celery worker 没起 / 没连上 Redis；起 worker 就行。
  3. 不能写就把 uvicorn 终端里的报错贴出来，我继续看。
"""
import os

from celery import Celery


def _env_truthy(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in ("1", "true", "yes", "on")


celery_app = Celery(
    "test_runner",
    broker="redis://127.0.0.1:6379/0",
    backend="redis://127.0.0.1:6379/1",
)

# EAGER 模式：.delay/.apply_async 改成同步执行；方便本地自测 & 调试
if _env_truthy("CELERY_TASK_ALWAYS_EAGER"):
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,  # 任务里抛异常直接传回调用方，不要静默吞掉
    )
    print("[celery_app] CELERY_TASK_ALWAYS_EAGER=1 —— 任务将在当前进程同步执行")

# ---------------------------------------------------------------------------
# 定时任务（beat schedule）
#
# 要让心跳探测生效，必须额外起一个 beat 进程：
#     celery -A celery_app beat --loglevel=INFO
# 同时再起 worker 消费：
#     celery -A celery_app worker --loglevel=INFO
# 两个进程分开跑。也可以用 `--beat` 合并起（开发期）：
#     celery -A celery_app worker --beat --loglevel=INFO
#
# DEVICE_PROBE_INTERVAL_SEC 环境变量可以调探测频率，默认 30s。
# ---------------------------------------------------------------------------
_probe_interval = float(os.getenv("DEVICE_PROBE_INTERVAL_SEC", "30") or 30)
celery_app.conf.beat_schedule = {
    "probe_devices_every_30s": {
        "task": "tasks.probe_devices",
        "schedule": _probe_interval,
        "options": {"expires": max(_probe_interval - 1, 5)},  # 积压就丢，别堆队列
    },
}
celery_app.conf.timezone = os.getenv("CELERY_TIMEZONE", "Asia/Shanghai")

celery_app.autodiscover_tasks(["tasks"])
import tasks.run_test_task  # noqa: F401 — 注册 Celery 任务
import tasks.probe_devices  # noqa: F401
import tasks.ai_tasks  # noqa: F401
