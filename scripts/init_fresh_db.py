"""全新数据库初始化 / 迁移引导（幂等，可重复跑）。

背景
----
本项目的 alembic 迁移链**不包含核心表（projects 等）的创建**——历史上这些表
是靠 ORM `create_all` 攒出来的，迁移只负责后续增量。因此在一个**全新空库**上
直接 `alembic upgrade head` 会在第一条迁移就因外键指向不存在的 `projects` 而失败。

这个脚本把"全新库"和"已有库"两种情况统一处理：

- **全新库**（没有 `alembic_version` 表）：
    1. `Base.metadata.create_all()` 按外键依赖顺序建好全部表；
    2. seed 平台固定的 6 个角色（RBAC 依赖它们，否则 admin 也没权限）；
    3. `alembic stamp head` 把版本标记到最新，之后新增的迁移能正常增量执行。
- **已有库**（有 `alembic_version` 表）：
    走正常的 `alembic upgrade head` 增量迁移。

用法：
    python scripts/init_fresh_db.py

被 docker/docker-entrypoint.sh 在 FastAPI 角色启动前自动调用，
用来替代原先裸跑的 `alembic upgrade head`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 先加载 .env，保证在任意终端直跑都能连到与 app 一致的库（DB 连接纯 env 驱动）。
from scripts._env import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import inspect  # noqa: E402

from database import models  # noqa: F401,E402  —— import 触发所有 model 注册到 metadata
from database.base import Base  # noqa: E402
from database.db import _resolve_db_conf  # noqa: E402
from database.engine import get_engine  # noqa: E402

# 平台固定 6 角色（与 20260504_0001_pm_redesign_phase1.py 的 SEED_ROLES 保持一致）
SEED_ROLES = [
    {"code": "admin", "name": "管理员", "description": "全平台读写 + 成员管理"},
    {"code": "dev", "name": "开发", "description": "认领 dev 任务，修复 bug"},
    {"code": "test", "name": "测试", "description": "执行测试，建 bug，出报告"},
    {"code": "pm", "name": "产品", "description": "需求管理，PM 验收 gate"},
    {"code": "ui", "name": "UI", "description": "走查任务，设计稿资产"},
    {"code": "ops", "name": "运维", "description": "环境探活，发版部署"},
]


def _alembic_config():
    from alembic.config import Config

    return Config(str(ROOT / "alembic.ini"))


def _seed_roles() -> None:
    """幂等 seed 6 个角色。"""
    from database.db import DB
    from database.models import Role

    db = DB()
    try:
        existing = {r.code for r in db.session.query(Role).all()}
        added = []
        for row in SEED_ROLES:
            if row["code"] not in existing:
                db.session.add(Role(**row))
                added.append(row["code"])
        if added:
            db.session.commit()
        print(f"[init_fresh_db] 角色 seed 完成，新增: {added or '（无，已存在）'}")
    finally:
        db.close()


def main() -> None:
    from alembic import command

    engine = get_engine(_resolve_db_conf())
    already_initialized = inspect(engine).has_table("alembic_version")

    if already_initialized:
        # 已有库：正常增量迁移
        print("[init_fresh_db] 检测到 alembic_version，执行增量迁移 alembic upgrade head ...")
        command.upgrade(_alembic_config(), "head")
        print("[init_fresh_db] ✅ 增量迁移完成")
        return

    # 全新库：建表 + seed 角色 + stamp
    print("[init_fresh_db] 空库，用 ORM 模型创建全部表 ...")
    Base.metadata.create_all(engine)
    print(f"[init_fresh_db] ✅ 已创建 {len(Base.metadata.tables)} 张表")

    _seed_roles()

    print("[init_fresh_db] alembic stamp head（标记版本，之后新增迁移可增量执行）...")
    command.stamp(_alembic_config(), "head")
    print("[init_fresh_db] ✅ 全新库初始化完成")
    print("[init_fresh_db] 提示：登录前请执行 `python scripts/seed_admin.py` 创建 admin 账号")


if __name__ == "__main__":
    main()
