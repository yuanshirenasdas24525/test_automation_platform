# config/pytest_config.py
import json
import pytest
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from src.database.models.test_step_report import TestStepReport
from src.utils.logger import LOGGER
from src.database.db import DB

db = DB()



# 数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./data/db/sqlite.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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

        # 2. 关键点：即便 cases 为空，也要注入一个 [None] 或空列表
        # 否则 pytest 发现函数有参数但没数据，会直接报错“not found”
        if cases:
            metafunc.parametrize("case", cases)
        else:
            # 这是一个占位符，防止收集失败
            metafunc.parametrize("case", [None], ids=["no_cases_found"])


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when != "call":
        return

    config = item.config
    report_id = config.getoption("--report_id")

    if not report_id:
        return

    try:
        case_name = report.nodeid.split("::")[-1]

        step_data = TestStepReport(
            report_id=int(report_id),
            step_name=case_name,
            status=report.outcome,
            duration=report.duration,
            create_time=datetime.now()
        )

        props = dict(report.user_properties)

        step_data.action = props.get("action")
        step_data.target = props.get("target")
        step_data.input_data = props.get("input_data")
        step_data.output_data = props.get("output_data")
        step_data.status_code = props.get("status_code")
        step_data.extract_values = props.get("extract_values")
        step_data.assert_result = props.get("assert_result")
        step_data.page_info = props.get("page_info")

        if report.failed:
            step_data.error_message = str(report.longrepr)

        db.session.add(step_data)
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        LOGGER.error(f"存储测试结果失败: {str(e)}")
    finally:
        db.session.close()


