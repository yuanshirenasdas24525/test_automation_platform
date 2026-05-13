"""
共用依赖（FastAPI `Depends` 的目标）。

- `get_db`：每请求一个 DB session；正常返回时自动 commit，异常时 rollback，然后 close。
- `DBDep`：用 `typing.Annotated` 封好的依赖别名，路由里 `db: DBDep` 比 `db: DB = Depends(get_db)` 省几个字。
- `get_current_user` / `CurrentUserDep`：JWT Bearer Token 解析当前用户。
"""
from __future__ import annotations

from typing import Annotated, Generator

from fastapi import Depends

from database.db import DB


def get_db() -> Generator[DB, None, None]:
    """
    请求级 DB session。语义：
    - 路由函数走完没抛，统一在这里 commit 一次；
    - 抛了就 rollback；
    - 结束一定 close。

    路由内部**尽量不要**自己再调 `db.session.commit()`。如果非要手动 flush
    某一步（比如需要 `db.session.refresh()`），commit 也留给这里兜底。
    """
    db = DB()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 简写别名：`db: DBDep` 比 `db: DB = Depends(get_db)` 更干净。
DBDep = Annotated[DB, Depends(get_db)]


# ---------------------------------------------------------------------------
# 认证依赖：从 server.api.auth 导出，避免循环 import
# ---------------------------------------------------------------------------
from server.api.auth import get_current_user  # noqa: E402

from database.models import User  # noqa: E402

CurrentUserDep = Annotated[User, Depends(get_current_user)]
