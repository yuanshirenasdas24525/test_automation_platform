import os
import shlex
import subprocess
from config.settings import ProjectPaths

def run(case=None, extra_args=None):
    """
    运行性能测试（Locust）
    - case 可传 locust 脚本，例如: ./src/core/load_test/locust_tasks.py
    - 通过环境变量 LOCUST_HOST 指定被测系统 host
    """
    locust_file = case or "./src/core/load_test/locust_tasks.py"
    host = os.environ.get("LOCUST_HOST", "http://localhost:8000")

    cmd = f"locust -f {shlex.quote(locust_file)} --host={shlex.quote(host)}"
    if extra_args:
        cmd += " " + " ".join(extra_args)

    print(f"[INFO] Start Locust: {cmd}")
    # 前台运行，便于查看日志；如需后台可改为 Popen
    return subprocess.call(cmd, shell=True)