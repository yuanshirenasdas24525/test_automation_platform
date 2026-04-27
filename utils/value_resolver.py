"""v2 step runner 统一的 value 解析工具。

老代码（core/mobile/actions/value_resolver.py::ValueResolver）是围绕 AppAction.app_steps
那条**死代码路径**设计的 —— 依赖 ParameterCache、DB 连接、extract_var 等概念，
跟 v2 StepRunner 那套 ExecutionContext 对不上。这里写一个薄的、ctx 友好的版本，
把老代码里最有用的三个特性捞出来：

  - `${var}` 变量替换（所有 prefix 都先走一遍，这样 sql: / function: 里也能嵌变量）
  - `sql:<query>`：查 target DB（需要注入 DB 连接），返回第一行（单元组）或单值
  - `function:<name>(...)`：调 utils.function_executor 里注册的函数
  - 非字符串：原样返回

使用方式（在 step runner 里）：

    from utils.value_resolver import resolve_value
    real_value = resolve_value(config.get("value"), ctx)

如果上层想让 `sql:` 生效，要给 ctx 喂一个 db 连接（duck-type：实现 `fetchone(query)` 方法）：

    ctx.set_var("_db", db_conn)

目前平台自带的 target DB 注入还没接通，`sql:` 会直接报错并给出提示，不会静默失败 ——
这是有意的：静默吞掉用户期望的 DB 查询会导致极难排查的断言错位。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ctx.vars 里用来传 DB 连接的约定 key。外部可以在 env / case 初始化时塞一个。
CTX_DB_KEY = "_db"


def resolve_value(raw: Any, ctx: Any, *, db: Any = None) -> Any:
    """解析一个值。

    ctx：ExecutionContext 或任意有 `.vars` 字典属性的对象。传 dict 也行。
    db：显式的 DB 连接；不传时从 `ctx.vars[CTX_DB_KEY]` 找；都没有则 `sql:` 报错。

    返回：解析后的值（可能是任意类型，比如 function: 返回 int、sql: 返回 tuple）。
    """
    if not isinstance(raw, str):
        return raw

    pool = _ctx_pool(ctx)

    # 先把 ${var} 替换掉 —— 这样 "sql:select ... where id=${uid}" / "function:gen(${x})"
    # 这类组合写法都能用
    from utils.platform_utils import rep_expr  # 延迟 import：避免 utils 循环
    replaced = rep_expr(raw, pool)

    # 检测未解析的 ${var}：rep_expr 找不到 key 时会原样保留 ${...}。
    # 这是个常见用户坑（变量名拼错 / 上一步没 extract 到 / 跨 case 引用），
    # 静默会让步骤拿到字面量 "${code}" 跑得稀里糊涂；这里 WARN 一下。
    import re as _re
    leftover = _re.findall(r"\$\{[^}\n]+\}", replaced)
    if leftover:
        logger.warning(
            "resolve_value: 以下 ${var} 占位未在变量池里找到，会原样保留 -> %s "
            "（请检查 env.variables / case.variables / 上一步 extract）",
            leftover,
        )

    # 1) sql:
    if replaced.startswith("sql:"):
        conn = db if db is not None else pool.get(CTX_DB_KEY)
        if conn is None:
            # 历史现状：平台还没自动注入 target DB。给出 actionable 提示。
            raise RuntimeError(
                f"value 使用了 sql: 前缀但当前 ctx 没有 DB 连接。原始值={raw!r}。\n"
                f"如何接通：\n"
                f"  - pre_hook 里跑一条 type='script' 的 hook，往 ctx 写 _db；\n"
                f"  - 或者在 env/case 初始化时 ctx.set_var({CTX_DB_KEY!r}, <conn>)。\n"
                f"目前如果你只是想做参数化，建议先用 function: 或 ${{var}}。"
            )
        query = replaced[4:].strip()
        logger.info("resolve_value 执行 SQL: %s", _truncate(query))
        try:
            return conn.fetchone(query)
        except AttributeError as exc:
            raise RuntimeError(
                f"注入的 DB 连接没有 fetchone 方法：{type(conn).__name__}"
            ) from exc

    # 2) function:
    if replaced.startswith("function:"):
        from utils.function_executor import exec_func
        # 约定：把整个变量池作为第一个位置参 传给函数。老函数里有些靠这个读 extra_pool
        # （比如 converter / assert_amount_* 都从 args[0] 拿 extra_pool）。
        try:
            return exec_func(replaced, pool)
        except Exception as exc:
            logger.error("function 调用失败 %s: %s", _truncate(replaced), exc)
            raise

    # 3) 纯字符串：rep_expr 已经做了 ${var} 替换，直接返回
    return replaced


def resolve_value_deep(raw: Any, ctx: Any, *, db: Any = None) -> Any:
    """递归解析 dict / list 里的每个字符串 value，字符串外的类型原样保留。

    比较适合 step.config 这种嵌套 JSON 的场景（比如传给 `touch_action` 的
    list of dict）。
    """
    if isinstance(raw, dict):
        return {k: resolve_value_deep(v, ctx, db=db) for k, v in raw.items()}
    if isinstance(raw, list):
        return [resolve_value_deep(v, ctx, db=db) for v in raw]
    return resolve_value(raw, ctx, db=db)


# ------------------------------------------------------------
# internal
# ------------------------------------------------------------
def _ctx_pool(ctx: Any) -> dict:
    """兼容：传 ExecutionContext、传 dict、传 None 都不崩。"""
    if ctx is None:
        return {}
    if isinstance(ctx, dict):
        return ctx
    vars_attr = getattr(ctx, "vars", None)
    if isinstance(vars_attr, dict):
        return vars_attr
    return {}


def _truncate(s: str, n: int = 120) -> str:
    return s if len(s) <= n else s[:n] + "..."
