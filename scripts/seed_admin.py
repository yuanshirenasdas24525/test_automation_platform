"""种子脚本：创建默认 admin 用户（密码 123456）。

运行一次即可：
  python3 scripts/seed_admin.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bcrypt

from database.db import DB
from database.models import Role, User


def seed_admin():
    db = DB()

    existing = db.session.query(User).filter(User.username == "admin").first()
    if existing is not None:
        if not existing.password_hash:
            existing.password_hash = bcrypt.hashpw(
                "123456".encode("utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
            existing.is_active = True
            if not existing.roles:
                roles = db.session.query(Role).all()
                existing.roles = list(roles)
            db.session.commit()
            print("admin 已存在，已设置密码")
        else:
            print("admin 已存在且已设置密码，跳过")
        db.close()
        return

    all_roles = db.session.query(Role).all()
    hashed = bcrypt.hashpw("123456".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    admin = User(
        username="admin",
        full_name="管理员",
        is_active=True,
        password_hash=hashed,
    )
    admin.roles = list(all_roles)
    db.session.add(admin)
    db.session.commit()
    print(f"已创建 admin 用户（密码: 123456，角色: {[r.code for r in all_roles]}）")
    db.close()


if __name__ == "__main__":
    seed_admin()
