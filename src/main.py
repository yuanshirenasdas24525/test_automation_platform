# -*- coding:utf-8 -*-
import argparse
import sys
import os
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
print("PROJECT_ROOT =>", PROJECT_ROOT)
print("sys.path[0] =>", sys.path[0])

# 各测试类型的 Runner（策略）
from src.runners import (
    api_runner,
    web_ui_runner,
    mobile_runner,
    load_runner,
)




TEST_TYPE_MAP = {
    "api": api_runner.run,
    "web": web_ui_runner.run,
    "mobile": mobile_runner.run,
    "load": load_runner.run,
}

def main():
    parser = argparse.ArgumentParser(description="自动化测试平台入口")
    parser.add_argument(
        "-t", "--type", required=True, choices=TEST_TYPE_MAP.keys(),
        help="测试类型: api / web / mobile / load"
    )
    parser.add_argument(
        "-c", "--case",
        help="测试用例路径（模块/类/方法），例如 ./tests/api/test_api.py::TestApi::test_case"
    )
    parser.add_argument(
        "--extra", nargs=argparse.REMAINDER,
        help="透传给 pytest/locust 的额外参数。例如: --extra -q -k test_login"
    )

    args = parser.parse_args()

    runner_func = TEST_TYPE_MAP[args.type]
    # 由具体 runner 决定如何组织/补全参数
    return runner_func(case=args.case, extra_args=args.extra)

if __name__ == "__main__":
    sys.exit(main())