"""开发脚本用的极简 .env 加载器（无第三方依赖）。

背景：DB 连接改成纯环境变量驱动后，在**没 source .env 的终端**里直接跑
`python scripts/seed_admin.py` 之类会因为缺 DB_* 而连不上/报错。让这些脚本
开头调一次 load_dotenv()，就能和 app / start-dev 连同一个库，避免"连错库"。

规则：已存在的环境变量优先（不覆盖），文件不存在则静默跳过。
"""
from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | Path | None = None) -> bool:
    """把项目根的 .env 读进 os.environ。返回是否加载了文件。"""
    env_path = Path(path) if path else _PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return False
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:  # 已有环境变量优先，不覆盖
            os.environ[key] = val
    return True
