"""/api/system/* 系统/服务健康检查。

提供一个 `GET /api/system/services` 端点，一次性返回整个平台运行依赖的健康状态，
前端工作台的"服务状态"卡片消费这个接口。

我们区分两类服务：

  - required=True   挂了就跑不通 → 前端卡片红警告
    * fastapi     —— 这个接口本身能响应就说明 FastAPI 活着（永远 OK）
    * database    —— `SELECT 1`，连不上或查询超时就挂
    * redis       —— celery broker，挂了任务进不去队列
    * celery_worker —— 有 worker 才能消费任务，否则报告永远 "running"

  - required=False  挂了只是降级，不影响主流程 → 前端黄提示
    * allure_cli  —— HTML 报告生成依赖（不装也能跑，`_run_allure_generate` 自己会跳过）

返回信封：
```
{
  "status": "success",
  "data": {
    "overall": "healthy" | "degraded" | "down",
    "checked_at": "2026-04-21T12:34:56",
    "services": [
      {"key": "database", "name": "数据库", "required": true,
       "status": "up" | "down" | "unknown", "detail": "...", "latency_ms": 3.2}
      ...
    ]
  }
}
```

设计原则：每个 check 都有超时，不能因为单个服务卡住整个响应。
"""
from __future__ import annotations

import os
import shutil
import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter

from server.api.deps import DBDep
from utils.appium_probe import probe_appium as _probe_appium

router = APIRouter(prefix="/system", tags=["system"])


# ---------------------------------------------------------------------------
# 单个服务检查：每个返回 (status, detail, latency_ms)
# status: "up" | "down" | "unknown"
# ---------------------------------------------------------------------------
def _check_database(db) -> tuple[str, str, float]:
    """SELECT 1 —— 能执行就算活着。"""
    t0 = time.perf_counter()
    try:
        # SQLHandler.query 走 raw SQL，兼容 SQLite/MySQL/PostgreSQL
        db.sql.query("SELECT 1 AS ok", {})
        lat = (time.perf_counter() - t0) * 1000
        return "up", "连接正常", lat
    except Exception as exc:
        lat = (time.perf_counter() - t0) * 1000
        return "down", f"查询失败: {exc}", lat


def _check_redis() -> tuple[str, str, float]:
    """ping celery broker URL。连不上 = worker 肯定也废。"""
    t0 = time.perf_counter()
    try:
        # 延迟 import：没装 redis 包时不要整个模块挂
        import redis  # type: ignore

        # 复用 celery_app 里的 broker URL，避免两处写死不一致
        from celery_app import celery_app

        broker_url = celery_app.conf.broker_url or "redis://127.0.0.1:6379/0"
        client = redis.Redis.from_url(broker_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        lat = (time.perf_counter() - t0) * 1000
        # 扒出 host:port/db 给前端显示用，藏掉密码（如果有）
        u = urlparse(broker_url)
        host = u.hostname or "?"
        port = u.port or 6379
        db_idx = u.path.lstrip("/") or "0"
        return "up", f"{host}:{port} (db {db_idx})", lat
    except Exception as exc:
        lat = (time.perf_counter() - t0) * 1000
        return "down", f"ping 失败: {exc}", lat


def _check_celery_worker() -> tuple[str, str, float]:
    """`inspect().ping()` 发广播到 workers；有响应就算存活。

    注意：这个调用本身是**阻塞**的，控制台里没 worker 时会等满 timeout。
    用 ThreadPoolExecutor 套一层超时兜底。
    """
    t0 = time.perf_counter()

    def _do_ping() -> dict | None:
        from celery_app import celery_app

        # EAGER 模式下不存在"远端 worker"，直接按"不需要"返回
        if celery_app.conf.task_always_eager:
            return {"__eager__": "EAGER 模式：任务在 uvicorn 进程同步执行"}
        insp = celery_app.control.inspect(timeout=1.5)
        return insp.ping()  # 返回 {worker_name: {"ok": "pong"}, ...} 或 None

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_do_ping)
            result = fut.result(timeout=3.0)
        lat = (time.perf_counter() - t0) * 1000
        if result is None or not result:
            return "down", "没有 worker 响应 ping（未启动或连不上 broker）", lat
        if "__eager__" in result:
            # EAGER 模式特殊路径
            return "up", result["__eager__"], lat
        worker_names = list(result.keys())
        return (
            "up",
            f"{len(worker_names)} 个 worker 在线: {', '.join(worker_names[:3])}"
            + ("…" if len(worker_names) > 3 else ""),
            lat,
        )
    except FutureTimeout:
        lat = (time.perf_counter() - t0) * 1000
        return "down", "ping 超时（通常是 broker 连不上）", lat
    except Exception as exc:
        lat = (time.perf_counter() - t0) * 1000
        return "unknown", f"检查异常: {exc}", lat


def _check_allure_cli() -> tuple[str, str, float]:
    """allure 是可选：没装就降级，装了打印版本。"""
    t0 = time.perf_counter()
    allure_bin = shutil.which("allure")
    lat = (time.perf_counter() - t0) * 1000
    if not allure_bin:
        return "down", "未安装 allure CLI（HTML 报告无法生成，其它功能不受影响）", lat
    return "up", allure_bin, lat


# _probe_appium 已抽到 utils/appium_probe.py（避免 Celery worker 拖 FastAPI 包）。
# 这里顶部再 import 回来别名 _probe_appium，保持下游代码不动。


def _check_appium_servers(db) -> tuple[str, str, float]:
    """遍历 devices 表里 idle/busy 状态（即预期"在线"）的设备，
    对每台的 agent_host:appium_port 做 `/status` 探测（TCP + Appium 2/1 base path）。

    状态判定：
      - 没有任何候选设备  → unknown（"还没注册设备"）
      - 全部都 ok         → up
      - 部分 ok           → degraded（体现在 detail 里，但 status 依然 up，
                             因为 required=False 不阻塞，不值得升级成 down）
      - 全部失败          → down
    """
    t0 = time.perf_counter()
    try:
        from database.models import Device
    except Exception as exc:  # noqa: BLE001
        lat = (time.perf_counter() - t0) * 1000
        return "unknown", f"Device model import 失败: {exc}", lat

    # 只看 idle + busy，这些是"理论上应该 online"的设备；offline 不检查
    try:
        rows = (
            db.session.query(Device)
            .filter(Device.status.in_(["idle", "busy"]))
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        lat = (time.perf_counter() - t0) * 1000
        return "unknown", f"查询 devices 失败: {exc}", lat

    if not rows:
        lat = (time.perf_counter() - t0) * 1000
        return "unknown", "尚未注册任何 idle/busy 设备", lat

    total = len(rows)
    up_count = 0
    failures: list[str] = []

    for dev in rows:
        host = dev.agent_host or "localhost"
        port = dev.appium_port or 4723
        ok, detail = _probe_appium(host, port)
        if ok:
            up_count += 1
        else:
            failures.append(f"{dev.udid}@{host}:{port}（{detail}）")

    lat = (time.perf_counter() - t0) * 1000
    if up_count == total:
        return "up", f"{total} 台 Appium 全部在线", lat
    if up_count == 0:
        return (
            "down",
            f"{total} 台全部不可达：{'; '.join(failures[:3])}"
            + ("…" if len(failures) > 3 else ""),
            lat,
        )
    return (
        "up",  # 有一台能用就算 up；问题设备写在 detail
        f"{up_count}/{total} 台在线。离线：{'; '.join(failures[:3])}"
        + ("…" if len(failures) > 3 else ""),
        lat,
    )


# ---------------------------------------------------------------------------
# 聚合路由
# ---------------------------------------------------------------------------
@router.get("/services")
def get_services_status(db: DBDep) -> dict[str, Any]:
    """一次性把所有依赖服务的状态回给前端。"""
    services: list[dict[str, Any]] = []

    # 1. FastAPI：能走到这里说明活着。没有 latency 概念。
    services.append(
        {
            "key": "fastapi",
            "name": "FastAPI 后端",
            "required": True,
            "status": "up",
            "detail": "服务本身响应正常",
            "latency_ms": None,
        }
    )

    # 2. 数据库
    status, detail, lat = _check_database(db)
    services.append(
        {
            "key": "database",
            "name": "数据库",
            "required": True,
            "status": status,
            "detail": detail,
            "latency_ms": round(lat, 2),
        }
    )

    # 3. Redis（celery broker）
    status, detail, lat = _check_redis()
    services.append(
        {
            "key": "redis",
            "name": "Redis (Celery Broker)",
            "required": True,
            "status": status,
            "detail": detail,
            "latency_ms": round(lat, 2),
        }
    )

    # 4. Celery worker
    status, detail, lat = _check_celery_worker()
    services.append(
        {
            "key": "celery_worker",
            "name": "Celery Worker",
            "required": True,
            "status": status,
            "detail": detail,
            "latency_ms": round(lat, 2),
        }
    )

    # 5. Allure CLI（可选）
    status, detail, lat = _check_allure_cli()
    services.append(
        {
            "key": "allure_cli",
            "name": "Allure CLI",
            "required": False,
            "status": status,
            "detail": detail,
            "latency_ms": round(lat, 2),
        }
    )

    # 6. Appium Servers（可选，App 自动化依赖；没注册过设备则 unknown）
    status, detail, lat = _check_appium_servers(db)
    services.append(
        {
            "key": "appium_servers",
            "name": "Appium Servers",
            "required": False,
            "status": status,
            "detail": detail,
            "latency_ms": round(lat, 2),
        }
    )

    # 总体状态：任何 required 挂 → down；可选挂 → degraded；全 up → healthy
    overall = "healthy"
    for s in services:
        if s["status"] == "up":
            continue
        if s["required"]:
            overall = "down"
            break
        overall = "degraded"  # 非必需挂了降级，继续看后面的 required

    return {
        "status": "success",
        "data": {
            "overall": overall,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "services": services,
        },
    }
