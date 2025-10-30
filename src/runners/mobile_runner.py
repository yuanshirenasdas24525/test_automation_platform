import os
import sys
import pytest
from src.runners._common import resolve_report_dirs

def run(case=None, extra_args=None, ci_mode=True, alluredir=None):
    """
    运行 mobile UI 测试（pytest + allure）
    依赖外部 Appium 服务（容器内 entrypoint 可自动启动）
    """
    report_data_dir, report_dir = resolve_report_dirs(alluredir)

    pytest_args = []
    if case:
        pytest_args.append(case)
    else:
        pytest_args.append("./tests/android_ui")

    pytest_args.append(f"--alluredir={report_data_dir}")

    if extra_args:
        pytest_args.extend(extra_args)

    exit_code = pytest.main(pytest_args)

    os.system(f"allure generate {report_data_dir} -o {report_dir} --clean")
    if not ci_mode and os.getenv("CI") != "true":
        os.system(f"allure serve {report_data_dir} -o {report_dir} -h 127.0.0.1 -p 52357")

    return exit_code