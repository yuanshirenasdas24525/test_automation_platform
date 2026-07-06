"""SqlStepRunner：执行 SQL 语句（DELETE/UPDATE/INSERT/SELECT），主要用于 teardown 清理。

设计动机
========
平台原本只有「`sql:` 值前缀」做查询断言（read-only，resolve_value 里 `conn.fetchone`），
没有一个能**写库并提交**的 step，导致 teardown 清理（`DELETE FROM x WHERE ...`）无处可跑。
本 Runner 补上这块，作为 step_type='sql' 的内置 Runner（CLAUDE.md 规定的扩展方式）：

    config = {
        "sql": "DELETE FROM orders WHERE id = ${orderId}; DELETE FROM users WHERE id = ${userId}",
        # 或者用 "query" 字段，二者等价
        "commit": true,   # 默认 true：写操作要提交；只读校验可设 false
    }

约定：
  - 多条语句用 `;` 分隔，逐条执行；
  - 语句里的 `${var}` 会先用 ctx.vars 替换（清理常引用前面 extract 出来的 id）；
  - 需要 ctx.vars['_db']（CaseExecutor._inject_target_db 已注入 target DB）；没有就 SKIP，
    只 warn 不报错——teardown 不该因为没配 DB 把用例搞挂；
  - Runner 永不 raise：异常包装成 StepResult（FAILED/ERROR），符合 protocol 不变量。
"""
from __future__ import annotations

import re
from typing import Any

from runners.context.execution_context import ExecutionContext
from runners.protocol import BaseStepRunner, StepResult, StepStatus
from utils.platform_utils import rep_expr
from utils.value_resolver import resolve_value


class SqlStepRunner(BaseStepRunner):
    step_types = ("sql", "sql_query")

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        config = step.get("config") or {}
        raw_sql = str(config.get("sql") or config.get("query") or "").strip()
        do_commit = config.get("commit", True)

        result.action = "sql"
        if not raw_sql:
            result.status = StepStatus.SKIPPED
            result.error_message = "sql 步骤没有 sql/query 内容，跳过"
            return

        # ${var} 替换：teardown 常用 ${orderId} 这类前面 extract 出来的值。
        # function:xxx 可用于动态生成整段 SQL；sql: 前缀在 SQL step 中只表示
        # “这是一条查询语句”，这里剥掉前缀后执行，避免被 value_resolver 提前查库。
        resolved = self._resolve_sql_text(raw_sql, ctx)
        statements = [s.strip() for s in resolved.split(";") if s.strip()]
        result.input_data = {"sql": resolved, "statements": len(statements)}

        conn = ctx.vars.get("_db")
        if conn is None:
            # teardown 没配 target DB：不报错，提示用户去配置中心配 target_db
            result.status = StepStatus.SKIPPED
            result.error_message = (
                "未注入 target DB（ctx._db 为空），SQL 步骤跳过。"
                "如需启用清理/查库，请在配置中心配置 target_db。"
            )
            return

        executed = []
        for stmt in statements:
            self._exec_one(conn, stmt)
            executed.append(stmt)
        if do_commit:
            self._commit(conn)
        result.output_data = {"executed": executed}

    @staticmethod
    def _resolve_sql_text(raw_sql: str, ctx: ExecutionContext) -> str:
        if raw_sql.strip().startswith("function:"):
            return str(resolve_value(raw_sql, ctx) or "")
        resolved = rep_expr(raw_sql, dict(ctx.vars or {}))
        leftovers = re.findall(r"\$\{[^}\n]+\}", resolved)
        if leftovers:
            raise ValueError(
                "SQL 步骤存在未解析变量："
                f"{', '.join(leftovers)}。请确认变量已在环境变量、用例变量或前序提取中写入。"
            )
        if resolved.strip().startswith("sql:"):
            return resolved.strip()[4:].strip()
        return resolved

    @staticmethod
    def _exec_one(conn: Any, stmt: str) -> None:
        """执行一条语句。兼容两种注入对象：
          - DB 实例（有 .sql.execute）；
          - 直接是 SQLHandler / 任意有 execute(stmt) 的对象。
        """
        sql_handler = getattr(conn, "sql", None)
        if sql_handler is not None and hasattr(sql_handler, "execute"):
            sql_handler.execute(stmt)
            return
        if hasattr(conn, "execute"):
            conn.execute(stmt)
            return
        raise RuntimeError(f"注入的 DB 连接不支持 execute：{type(conn).__name__}")

    @staticmethod
    def _commit(conn: Any) -> None:
        for target in (conn, getattr(conn, "sql", None), getattr(conn, "session", None)):
            commit = getattr(target, "commit", None)
            if callable(commit):
                commit()
                return
