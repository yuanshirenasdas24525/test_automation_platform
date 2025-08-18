import os
import sys
import pytest
from src.runners._common import resolve_report_dirs

def run(case=None, extra_args=None, ci_mode=True, alluredir=None):
    """
    运行 API 测试（pytest + allure）
    ci_mode: True 表示在 CI/CD 环境，不启动交互式报告
    """
    report_data_dir, report_dir = resolve_report_dirs(alluredir)

    pytest_args = []
    if case:
        pytest_args.append(case)
    else:
        pytest_args.append("./tests/api")  # 默认目录

    # 加上 allure 输出目录
    pytest_args.append(f"--alluredir={report_data_dir}")

    # 透传额外参数
    if extra_args:
        pytest_args.extend(extra_args)

    exit_code = pytest.main(pytest_args)

    # 生成报告
    os.system(f"allure generate {report_data_dir} -o {report_dir} --clean")

    # 本地调试模式才启动 serve
    if not ci_mode and os.getenv("CI") != "true":
        os.system(f"allure serve {report_data_dir} -o {report_dir} -h 127.0.0.1 -p 52357")

    return exit_code