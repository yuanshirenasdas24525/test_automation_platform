import pytest
import time
from datetime import datetime
from common.context import ctx  # 导入你的数据库会话
from database.models import HookTestReport, HookTestStepReport  # 导入模型


# 全局收集器，用于暂存执行结果
class ResultCollector:
    def __init__(self):
        self.reports = []
        self.start_time = None
        self.project_id = None
        self.category = None


@pytest.fixture(scope="session", autouse=True)
def test_context(request):
    """通过命令行参数或环境变量获取项目ID和分类"""
    # 假设你在调用 pytest 时通过 --project_id 传入
    collector.project_id = request.config.getoption("--project_id")
    collector.category = request.config.getoption("--category")
    collector.start_time = datetime.now()


collector = ResultCollector()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()

    # 我们只关心 'call' 阶段（即实际执行阶段），忽略 setup 和 teardown
    if rep.when == "call":
        # 解析用例中可能带有的详细信息
        # item.obj 是具体的测试函数，可以从中读取自定义标记
        collector.reports.append({
            "name": item.name,
            "status": "pass" if rep.passed else "fail",
            "duration": rep.duration,
            "error_msg": str(rep.longrepr) if rep.failed else "",
            # 这里可以从用例对象里获取更多后端传过来的 context
            "url": getattr(item.obj, 'url', ''),
            "method": getattr(item.obj, 'method', '')
        })


def pytest_sessionfinish(session, exitstatus):
    """整个测试套件跑完后，执行数据库持久化"""
    db = ctx.db
    try:
        # 1. 计算总统计数据
        total = len(collector.reports)
        passed = len([r for r in collector.reports if r['status'] == 'pass'])
        failed = total - passed

        # 2. 写入主报告 TestReport
        main_report = HookTestReport(
            project_id=collector.project_id,
            category=collector.category,
            total_count=total,
            pass_count=passed,
            fail_count=failed,
            status="success" if failed == 0 else "fail",
            start_time=collector.start_time,
            end_time=datetime.now(),
            duration=(datetime.now() - collector.start_time).total_seconds()
        )
        db.add(main_report)
        db.flush()  # 拿到主报告 ID

        # 3. 批量写入步骤明细 TestStepReport
        step_objects = [
            HookTestStepReport(
                report_id=main_report.id,
                step_name=r['name'],
                status=r['status'],
                duration=r['duration'],
                error_message=r['error_msg'],
                url=r['url'],
                method=r['method']
            ) for r in collector.reports
        ]
        db.bulk_save_objects(step_objects)
        db.commit()
    finally:
        db.close()