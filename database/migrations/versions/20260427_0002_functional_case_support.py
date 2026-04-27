"""functional_case_support

为"功能用例"（人工执行 + 勾结果）提供存储支持：

  1. test_cases 加 `functional_spec` JSON 列。
     functional 类型用例没有 step_type 概念，只有"前置条件 / 测试步骤 /
     预期结果"三段文本说明，直接塞这一个 JSON 列里：
       {"preconditions": [...], "steps": [...], "expected": "..."}

  2. 新建 `functional_case_runs` 表，记录每次人工执行结果。
     一条 functional_case 可以被多次执行（回归测试每轮一次），所以是
     1 → N 关系，按 executed_at 倒序看历史。

设计取舍：
  - 没有把 functional 用例塞进 test_steps 表 —— functional 没有 step_type，
    硬塞会让 dispatcher / runner 不得不写一堆"if functional 跳过"的特例。
    单独一个 functional_spec JSON 列对存储和读取都更顺。
  - functional_case_runs 不挂在 TestReport 下（自动化报告聚合用），
    而是独立表 + batch_id 字段做轻量分组。后续如果要做"批次结果汇总"
    再加聚合查询。

Revision ID: func_case_000001
Revises: proj_stk_000001
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "func_case_000001"
down_revision: Union[str, None] = "proj_stk_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. test_cases 加 functional_spec
    with op.batch_alter_table("test_cases") as batch_op:
        batch_op.add_column(sa.Column("functional_spec", sa.JSON(), nullable=True))

    # 2. functional_case_runs：人工执行结果记录
    op.create_table(
        "functional_case_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # status 枚举：pending（占位，一般不入库）/ passed / failed / blocked / na
        sa.Column("status", sa.String(length=20), nullable=False),
        # 实际表现：自由文本，建议 < 2000 字
        sa.Column("actual_result", sa.Text(), nullable=True),
        # 备注 / Bug 链接
        sa.Column("note", sa.Text(), nullable=True),
        # 执行人。先存字符串（用户名 / 工号 / 邮箱），后续接用户系统再转 FK
        sa.Column("operator", sa.String(length=64), nullable=True),
        # 执行时间，DB 侧默认 now()
        sa.Column(
            "executed_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        # 批次 ID：把"一轮回归测试"里勾的若干结果归到一起。
        # 不强制 FK，前端生成 UUID 传过来即可，后端原样存。
        sa.Column("batch_id", sa.String(length=64), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_table("functional_case_runs")
    with op.batch_alter_table("test_cases") as batch_op:
        batch_op.drop_column("functional_spec")
