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
from datetime import datetime

import pytest

from database.db import DB
from database.models.test_step_report import TestStepReport
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


# -----------------------------------------------------------------------------
# pytest options / parametrize
# -----------------------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption("--cases_data", action="store", default="[]")
    parser.addoption("--report_id", action="store", help="主报告ID")
    parser.addoption("--category", action="store", help="测试类型: api/web/app")


def pytest_generate_tests(metafunc):
    if "case" in metafunc.fixturenames:
        # 1. 尝试获取数据
        data_str = metafunc.config.getoption("--cases_data")
        try:
            cases = json.loads(data_str)
        except Exception:
            cases = []

        # 2. 关键点：即便 cases 为空，也要注入一个 [None] 占位，
        # 否则 pytest 发现函数有参数但没数据，会直接报错 "not found"
        if cases:
            metafunc.parametrize("case", cases)
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

    step_data = TestStepReport(
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
    )
    if report.failed:
        step_data.error_message = str(report.longrepr)[:2000]

    # 每条用例一把独立 session —— 出错不污染别的用例
    db = DB()
    try:
        db.session.add(step_data)
        db.session.commit()
        print(
            f"[pytest_hook] 已写入 step_report report_id={report_id} "
            f"case={case_name} status={report.outcome}"
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
