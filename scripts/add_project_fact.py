"""往项目记忆层(project_contexts)手动加一条事实,供 AI 生成用例时参考。

适合录入那些"AI 靠猜不可靠、但一旦知道就能写对"的项目级约定,例如:
  - 会话模型(单会话/多会话)
  - 响应信封结构
  - 特殊鉴权约定、错误码风格等

用法(项目根目录):
    venv/bin/python scripts/add_project_fact.py <project_id> <context_type> "<标题>" "<内容>"

context_type 可选:business_rule / data_model / api_contract / term_definition /
              constraint / process_flow / dependency / user_scenario

例(把"单会话"事实写进项目 2):
    venv/bin/python scripts/add_project_fact.py 2 business_rule \\
      "登录会话模型:单会话" \\
      "同一账号每次登录都会踢掉上一次会话,只有最后一次登录的 token 有效,之前的全部失效(401 会话已失效)。测'多会话各自有效'必须用不同账号分别登录;断言旧 token 时应断言其已失效,而非仍有效。"
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main(project_id: int, ctx_type: str, title: str, content: str) -> None:
    from database.db import DB
    from database.models import ALL_CONTEXT_TYPES
    from server.services.context_service import save_contexts

    if ctx_type not in ALL_CONTEXT_TYPES:
        print(f"❌ context_type={ctx_type!r} 非法。可选:{sorted(ALL_CONTEXT_TYPES)}")
        sys.exit(1)

    db = DB()
    try:
        created = save_contexts(
            contexts=[{
                "context_type": ctx_type,
                "title": title,
                "content": content,
                "summary": content[:120],
                "importance": 5,
            }],
            project_id=project_id,
            source_type="manual",
            source_file="add_project_fact.py",
            session=db.session,
        )
        db.commit()
        if created:
            print(f"✅ 已写入项目 {project_id} 记忆层(id={created[0]}, {ctx_type})")
        else:
            print("⚠️ 未写入(可能与已有条目标题+类型重复,已自动跳过)")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)
    main(int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4])
