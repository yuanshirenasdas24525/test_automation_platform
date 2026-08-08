# config/pytest_config.py
"""pytest 全局钩子：每条用例跑完，把 TestStepReport 写进库。

坑记（2026-04 修复）：
  1. 以前在模块顶层搞了 `db = DB()` 一个**全局**会话，每个 hook 执行完还 `db.session.close()`
     —— 关了之后下一次测试再用，一旦连接池 / 事务异常就整批写不进去，而且 except 里
     的 rollback 也会对着关闭过的 session 报错；出了问题用户看不到 log 只看到库空空。
     改成 **每条用例一把新会话**，写完立刻 commit + close，钩子之间互不影响。
  2. `step_data.case_id = safe_json_dumps(props.get("case_id"))` 会把 int 包成字符串塞
     进 Integer 列。SQLite 再仁慈也会搞出 `1` vs `"1"` 查询不一致。改成老实直接塞
     原值 + 能转 int 才转。
  3. 打印写入条数到 stdout，celery worker log 里能直接看到，便于排查「为什么库里没数据」。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from database.db import DB
from database.models import (
    TestReport,
    UiContextArtifact,
    UiContextEvent,
    UiContextSession,
    UiStepContextLink,
)
from database.models.test_step_report import TestStepReport
from server.services.ui_recording_redaction import redact_context_payload
from utils.logger import LOGGER


# -----------------------------------------------------------------------------
# 小工具
# -----------------------------------------------------------------------------
def safe_json_dumps(data):
    if data is None:
        return None
    if isinstance(data, (dict, list)):
        return json.dumps(data, ensure_ascii=False)
    return str(data)


def _coerce_int(value):
    """Integer 列里想放 int；拿不到就返回 None，绝不落字符串。"""
    if value is None:
        return None
    if isinstance(value, bool):
        # 防御：bool 是 int 子类，别被意外存进去
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _step_records_from_props(props: dict) -> list[dict]:
    """从 pytest user_properties 里取出 CaseExecutor 写入的 step_N 明细。"""
    records: list[dict] = []
    pattern = re.compile(r"^step_(\-?\d+)$")
    for key, value in props.items():
        match = pattern.match(str(key))
        if not match or not isinstance(value, dict):
            continue
        item = dict(value)
        item["_order_key"] = int(match.group(1))
        records.append(item)
    return sorted(
        records,
        key=lambda item: (
            _coerce_int(item.get("step_order")) if _coerce_int(item.get("step_order")) is not None else item["_order_key"],
            _coerce_int(item.get("step_id")) or 0,
        ),
    )


def _duration_seconds(value) -> float | None:
    """StepResult 里是毫秒，数据库 duration 存秒。"""
    try:
        return float(value) / 1000
    except (TypeError, ValueError):
        return None


# -----------------------------------------------------------------------------
# pytest options / parametrize
# -----------------------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption("--cases_data", action="store", default="[]")
    parser.addoption("--report_id", action="store", help="主报告ID")
    parser.addoption("--category", action="store", help="测试类型: api/web/app")
    # 逐条即时自愈：某条用例失败就地诊断+修复+重试一次，再跑下一条。
    # 目的是阻断连锁污染（上游拿不到变量会让下游全挂），见 server/services/inline_heal.py
    parser.addoption("--ai_heal", action="store_true", default=False,
                     help="逐条自愈：用例失败时就地修复并重试一次")
    parser.addoption(
        "--ai_heal_model",
        action="store",
        default="",
        help="逐条自愈使用的项目级 AI 模型名称",
    )


# -----------------------------------------------------------------------------
# App 会话跨 case 持久化 —— 一轮 pytest 跑完统一收尾
# -----------------------------------------------------------------------------
def _calibrate_shared_accounts(session) -> None:
    """跑前校准：把配置中心声明的共享测试账号密码重置回配置值。

    动机：接口用例里有"改密码/删除"等破坏性操作，个别用例会在运行中改坏共享
    账号（如 demo_admin），导致后续所有登录它的用例级联 401。每轮开跑前归零一次，
    让污染不跨轮累积——这是测试环境该有的"每轮干净桌子"。

    作用对象：由 --report_id 定位到项目，读配置中心 default_parameters 的
    user_admin / password_admin，把平台 users 表里该账号的密码重置为配置密码。
    校准失败只记日志，绝不阻塞测试。
    """
    db = None
    try:
        report_id = session.config.getoption("--report_id")
        if not report_id:
            return
        import bcrypt
        from sqlalchemy import text
        from database.db import DB
        from utils.reload_config import config_center

        db = DB()
        pid = db.session.execute(
            text("SELECT project_id FROM test_reports WHERE id = :r"),
            {"r": int(report_id)},
        ).scalar()
        if pid is None:
            return
        try:
            config_center.reload(db.sql, project_id=pid, category="api")
        except Exception:  # noqa: BLE001
            pass
        dp = config_center.get("default_parameters", project_id=pid) or {}
        if not isinstance(dp, dict):
            return
        acct, pw = dp.get("user_admin"), dp.get("password_admin")
        if acct and pw:
            hashed = bcrypt.hashpw(str(pw).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            db.session.execute(
                text("UPDATE users SET password_hash = :h WHERE username = :u"),
                {"h": hashed, "u": acct},
            )
            db.session.commit()
            print(f"[跑前校准] 已重置共享账号 {acct} 密码为配置值")
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("[跑前校准] 失败（已忽略）：%s", exc)
        try:
            if db is not None:
                db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:  # noqa: BLE001
            pass


def pytest_sessionstart(session):  # noqa: ARG001
    """新一轮 pytest run 开始前，把 AppSessionRegistry singleton 重置一次。

    为什么要 reset：
      - Celery worker 是长期存活的进程，同一个 Python 解释器可能连续跑几次
        pytest.main；如果 singleton 不清掉，上一轮遗留的 closed session 或
        stale 引用可能混进这一轮（虽然理论上 close_all + _closed=True 会防御，
        但不如每轮一张干净桌子来得省心）。
      - 单测里用 `config.pytest_config` 插件时，同样能确保 registry 是新的。
    """
    try:
        from runners.app.session_registry import AppSessionRegistry
        # 先把可能残留的上一轮全部关掉（理论上 sessionfinish 已经关了，但 worker
        # 上一轮如果非正常结束会留尾巴，这里再补一刀）
        try:
            AppSessionRegistry.default().close_all()
        except Exception:  # noqa: BLE001
            pass
        AppSessionRegistry.reset()
    except ImportError:
        # session_registry 模块不存在（极端环境）不要让 pytest 起不来
        pass
    try:
        from tests.service_run_executor import reset_run_shared_vars
        reset_run_shared_vars()
    except ImportError:
        pass
    # 跑前校准：把配置的共享测试账号密码归零，避免上一轮用例改坏它、污染本轮
    _calibrate_shared_accounts(session)


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """一轮 pytest run 结束：把 AppSessionRegistry 里所有活着的 driver 全部 quit +
    release 设备。无论 exitstatus 如何都要关，资源泄漏比假阳性严重得多。
    """
    try:
        from runners.app.session_registry import AppSessionRegistry
        AppSessionRegistry.default().close_all()
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"[pytest_hook] AppSessionRegistry.close_all 异常: {exc}")


def pytest_generate_tests(metafunc):
    if "case" in metafunc.fixturenames:
        # 1. 尝试获取数据
        data_str = metafunc.config.getoption("--cases_data")
        try:
            cases = json.loads(data_str)
        except Exception:
            cases = []

        # 2. 单用例重复执行：按 case.repeat_count 把每条 case 展开成 N 份。
        # 每份带 _iteration / _iteration_total，让 service_run_executor 给
        # Allure 标题加“(第 i/N 次)”后缀，N 次各成独立报告条目。
        # 展开只发生在这里，不动 CaseExecutor / Dispatcher / Runner。
        expanded = []
        ids = []
        for c in cases:
            if not isinstance(c, dict):
                expanded.append(c)
                ids.append("case")
                continue
            try:
                n = int(c.get("repeat_count") or 1)
            except (TypeError, ValueError):
                n = 1
            n = max(1, min(n, 100))  # 兜底：至少 1，上限 100 防误填
            cid = c.get("id")
            if n <= 1:
                expanded.append(c)
                ids.append(f"case{cid}")
                continue
            for i in range(n):
                c2 = dict(c)
                c2["_iteration"] = i + 1
                c2["_iteration_total"] = n
                expanded.append(c2)
                ids.append(f"case{cid}#{i + 1}")

        # 3. 关键点：即便 cases 为空，也要注入一个 [None] 占位，
        # 否则 pytest 发现函数有参数但没数据，会直接报错 "not found"
        if expanded:
            metafunc.parametrize("case", expanded, ids=ids)
        else:
            metafunc.parametrize("case", [None], ids=["no_cases_found"])


# -----------------------------------------------------------------------------
# 每个用例执行完 → 写一条 TestStepReport
# -----------------------------------------------------------------------------
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    # 只在 call 阶段落库（setup/teardown 不重复写）
    if report.when != "call":
        return

    report_id_opt = item.config.getoption("--report_id")
    if not report_id_opt:
        return
    try:
        report_id = int(report_id_opt)
    except (TypeError, ValueError):
        LOGGER.warning(f"pytest_runtest_makereport: 非法 report_id={report_id_opt!r}")
        return

    case_name = report.nodeid.split("::")[-1]
    props = dict(report.user_properties)
    step_records = _step_records_from_props(props)
    if step_records:
        rows = []
        for item in step_records:
            error_message = item.get("error")
            if not error_message and item.get("status") in {"failed", "error"} and report.failed:
                error_message = str(report.longrepr)[:2000]
            rows.append(TestStepReport(
                report_id=report_id,
                case_id=_coerce_int(props.get("case_id")),
                step_id=_coerce_int(item.get("step_id")),
                step_name=item.get("step_name") or case_name,
                step_type=item.get("step_type"),
                status=item.get("status") or report.outcome,
                duration=_duration_seconds(item.get("duration_ms")),
                action=safe_json_dumps(item.get("action")),
                target=safe_json_dumps(item.get("target")),
                input_data=safe_json_dumps(item.get("input_data")),
                output_data=safe_json_dumps(item.get("output_data")),
                status_code=_coerce_int(item.get("status_code")),
                extract_values=safe_json_dumps(item.get("extract_values")),
                assertion_results=safe_json_dumps(item.get("assertion_results")),
                page_info=safe_json_dumps(item.get("page_info")),
                attachments=item.get("attachments"),
                error_message=error_message,
                create_time=datetime.now(),
            ))
    else:
        rows = [TestStepReport(
            report_id=report_id,
            case_id=_coerce_int(props.get("case_id")),
            step_name=case_name,
            status=report.outcome,          # passed / failed / skipped
            duration=float(report.duration) if report.duration is not None else None,
            action=safe_json_dumps(props.get("action")),
            target=safe_json_dumps(props.get("target")),
            input_data=safe_json_dumps(props.get("input_data")),
            output_data=safe_json_dumps(props.get("output_data")),
            status_code=_coerce_int(props.get("status_code")),
            extract_values=safe_json_dumps(props.get("extract_values")),
            assertion_results=safe_json_dumps(props.get("assertion_results")),
            page_info=safe_json_dumps(props.get("page_info")),
            create_time=datetime.now(),
        )]
        if report.failed:
            rows[0].error_message = str(report.longrepr)[:2000]

    # 每条用例一把独立 session —— 出错不污染别的用例
    db = DB()
    try:
        db.session.add_all(rows)
        db.session.flush()
        report_row = db.session.get(TestReport, report_id)
        context_session = (
            db.session.get(UiContextSession, report_row.context_session_id)
            if report_row is not None and report_row.context_session_id is not None
            else None
        )
        context_payloads = [
            dict(item.get("context") or {})
            for item in step_records
        ] if step_records else [{}]
        if context_session is not None:
            next_sequence = int(
                db.session.query(UiContextEvent.sequence_no)
                .filter(UiContextEvent.context_session_id == context_session.id)
                .order_by(UiContextEvent.sequence_no.desc())
                .limit(1)
                .scalar()
                or 0
            )
            limitations = list(context_session.limitations or [])
            for row, context_payload in zip(rows, context_payloads):
                row.context_session_id = context_session.id
                created_events: list[UiContextEvent] = []
                for raw_event in list(context_payload.get("events") or [])[:1000]:
                    if not isinstance(raw_event, dict):
                        continue
                    next_sequence += 1
                    occurred_at = raw_event.get("occurred_at")
                    try:
                        occurred = datetime.fromisoformat(str(occurred_at))
                    except (TypeError, ValueError):
                        occurred = datetime.now()
                    event = UiContextEvent(
                        context_session_id=context_session.id,
                        event_key=str(raw_event.get("event_key") or f"execution-{report_id}-{next_sequence}")[:80],
                        sequence_no=next_sequence,
                        event_type=str(raw_event.get("event_type") or "runner.event")[:80],
                        source=str(raw_event.get("source") or "runner")[:40],
                        severity=str(raw_event.get("severity") or "info")[:20],
                        step_id=_coerce_int(raw_event.get("step_id") or row.step_id),
                        occurred_at=occurred,
                        monotonic_ms=_coerce_int(raw_event.get("monotonic_ms")),
                        payload=redact_context_payload(dict(raw_event.get("payload") or {})),
                    )
                    db.session.add(event)
                    created_events.append(event)
                db.session.flush()
                if created_events:
                    row.context_event_from_seq = created_events[0].sequence_no
                    row.context_event_to_seq = created_events[-1].sequence_no
                before_artifact = None
                after_artifact = None
                for phase, artifact_type in (("before", "step_screenshot_before"), ("after", "step_screenshot_after")):
                    uri = str(context_payload.get(f"screenshot_{phase}") or "").strip()
                    if not uri:
                        continue
                    artifact = UiContextArtifact(
                        context_session_id=context_session.id,
                        context_event_id=created_events[-1].id if created_events else None,
                        artifact_type=artifact_type,
                        uri=uri,
                        mime_type="image/png",
                        metadata_json={"step_id": row.step_id, "step_name": row.step_name},
                    )
                    db.session.add(artifact)
                    db.session.flush()
                    if phase == "before":
                        before_artifact = artifact
                    else:
                        after_artifact = artifact
                limitations.extend(str(item) for item in context_payload.get("limitations") or [])
                db.session.add(UiStepContextLink(
                    context_session_id=context_session.id,
                    test_step_report_id=row.id,
                    event_from_seq=row.context_event_from_seq,
                    event_to_seq=row.context_event_to_seq,
                    screenshot_before_id=before_artifact.id if before_artifact else None,
                    screenshot_after_id=after_artifact.id if after_artifact else None,
                    summary={"status": row.status, "step_type": row.step_type},
                ))
            context_session.limitations = list(dict.fromkeys(limitations))[-100:]
            context_session.summary = {
                **(context_session.summary or {}),
                "last_sequence": next_sequence,
            }
        db.session.commit()
        print(
            f"[pytest_hook] 已写入 step_report report_id={report_id} "
            f"case={case_name} rows={len(rows)} status={report.outcome}"
        )
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        LOGGER.error(f"存储测试结果失败 report_id={report_id} case={case_name}: {exc}")
        print(f"[pytest_hook] FAIL report_id={report_id} case={case_name}: {exc}")
    finally:
        try:
            db.session.close()
        except Exception:
            pass
