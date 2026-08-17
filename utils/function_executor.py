# -*- coding:utf-8 -*-
from __future__ import annotations

from utils.platform_utils import execution_time_decorator

@execution_time_decorator
def exec_func(value, *args, **kwargs):
    """根据 ``function:`` 表达式调用脚本库函数。

    支持 ``function:foo``、``function:foo()`` 和
    ``function:foo(a, "b", 3)``。项目代码不再有内置函数回退；找不到脚本时立即
    报错，防止有人通过修改本文件偷偷给某条用例增加专用逻辑。
    """
    if not (isinstance(value, str) and value.startswith("function:")):
        return value

    raw = value.split("function:", 1)[1].strip()
    has_parens, f_name, parsed_args = _parse_func_call(raw)

    vars_pool = _vars_from_args(args)
    project_id = _project_id_from_pool(vars_pool)
    try:
        from utils.script_runtime import run_named_script
        found, result = run_named_script(
            f_name,
            kind="function",
            project_id=project_id,
            args=parsed_args if has_parens else [],
            vars=vars_pool,
        )
        if found:
            return result
    except Exception as e:
        raise Exception(f"执行页面脚本函数 '{f_name}' 时发生错误: {e}")

    available = ", ".join(_dynamic_function_names(project_id))
    raise ValueError(
        f"未找到脚本库函数 '{f_name}'（原始：{raw!r}）。"
        f"所有动态函数都必须来自脚本库；当前可用函数: {available or '（无）'}"
    )


def _parse_func_call(raw: str):
    """把 'foo' / 'foo()' / 'foo(a, "b", 3)' 解析成 (has_parens, name, [args])。

    引号内的逗号不会被切（比如 `foo("a, b", c)` → 两个参数 ['a, b', 'c']），
    用一个简单的 quote-aware tokenizer 实现，不引入正则反向断言堆。
    嵌套括号、反斜杠转义不支持 —— 用户场景是简单字面量 / 变量替换后的产物。
    """
    if "(" not in raw or not raw.endswith(")"):
        return False, raw.strip(), []

    name, _, rest = raw.partition("(")
    inner = rest[:-1]  # 剥掉末尾 ')'

    # quote-aware split on commas
    tokens: list[str] = []
    buf: list[str] = []
    quote: str | None = None  # 当前进入了哪种引号；None=不在引号内
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            tokens.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    # 总是把最后一段塞进去；空段在下面 parse loop 里 strip 后会被跳过
    tokens.append("".join(buf))

    parsed_args: list = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        # 去掉首尾匹配的引号
        if (token.startswith('"') and token.endswith('"')) or (
            token.startswith("'") and token.endswith("'")
        ):
            parsed_args.append(token[1:-1])
            continue
        # 字面量：尝试 int → float → 原样字符串
        try:
            parsed_args.append(int(token))
            continue
        except ValueError:
            pass
        try:
            parsed_args.append(float(token))
            continue
        except ValueError:
            pass
        parsed_args.append(token)
    return True, name.strip(), parsed_args


def _vars_from_args(args) -> dict:
    """从 exec_func 的兼容参数里找到变量池。

    v2 value_resolver 会把 ctx.vars 放在 args[0]；老 RequestDataProcessor
    会按 (sql_results, parent_data, extra_pool) 传参，变量池在 args[2]。
    """
    for item in args:
        if isinstance(item, dict) and (
            "_project_id" in item
            or "project_id" in item
            or "_run_shared_vars" in item
        ):
            return item
    return args[0] if args and isinstance(args[0], dict) else {}


def _project_id_from_pool(pool: dict) -> int | None:
    """从变量池里取项目 ID，用于优先匹配项目脚本。"""
    raw = pool.get("_project_id") or pool.get("project_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _dynamic_function_names(project_id: int | None) -> list[str]:
    """读取页面脚本函数名，仅用于错误提示。"""
    try:
        from utils.script_runtime import list_script_names
        return list_script_names("function", project_id=project_id)
    except Exception:
        return []
