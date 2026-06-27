"""种子脚本：创建 / 修复默认 admin 用户（密码 Test#123）。

运行一次即可：
  python3 scripts/seed_admin.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bcrypt

from database.db import DB
from database.models import Role, User

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Test#123"


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed_admin():
    db = DB()
    try:
        all_roles = db.session.query(Role).all()
        existing = db.session.query(User).filter(User.username == ADMIN_USERNAME).first()

        # 兼容旧逻辑：停用用户时曾把用户名改成 admin_xxxxxx。
        if existing is None:
            existing = (
                db.session.query(User)
                .filter(User.username.like(f"{ADMIN_USERNAME}_%"))
                .order_by(User.id.asc())
                .first()
            )

        if existing is not None:
            existing.username = ADMIN_USERNAME
            existing.full_name = existing.full_name or "管理员"
            existing.is_active = True
            existing.password_hash = _hash_password(ADMIN_PASSWORD)
            existing.roles = list(all_roles)
            db.session.commit()
            print(f"admin 已修复（密码: {ADMIN_PASSWORD}，角色: {[r.code for r in all_roles]}）")
            return

        admin = User(
            username=ADMIN_USERNAME,
            full_name="管理员",
            is_active=True,
            password_hash=_hash_password(ADMIN_PASSWORD),
        )
        admin.roles = list(all_roles)
        db.session.add(admin)
        db.session.commit()
        print(f"已创建 admin 用户（密码: {ADMIN_PASSWORD}，角色: {[r.code for r in all_roles]}）")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
