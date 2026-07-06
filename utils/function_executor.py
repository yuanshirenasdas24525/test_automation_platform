# -*- coding:utf-8 -*-
from __future__ import annotations

import inspect

from database.redis import clear_cache
from utils.platform_utils import execution_time_decorator
from utils.captcha import solve_captcha

@execution_time_decorator
def exec_func(value, *args, **kwargs):
    """
    通用函数执行器，根据 "function:" 前缀调用注册中心函数。

    ── 三种写法 ──
        function:foo                    → foo(*args, **kwargs)（"无括号"老行为）
        function:foo()                  → foo()（pool 仅当 foo 接受 extra_pool=pool 时才注入）
        function:foo(a, "b", 3)         → foo(a, "b", 3)（用户 args 为主）
        function:foo(${code}, ${name})  → ${var} 由 value_resolver 先解析，
                                          再切片成 foo("my_code", "my_name")

    ── 字面量类型 ──
        - 数字串：自动转 int / float，如 `function:foo(3, 1.5)` → (3, 1.5)
        - 双 / 单引号包起来：去引号后按字符串
        - 其它：保留字符串字面量（典型是 ${var} 替换出来的内容）

    ── pool 怎么传给函数 ──
      `value_resolver.resolve_value` 调用本函数时会把 ctx.vars 字典作为第一个
      位置参数（args[0]）传进来。两条不同的注入策略：
        A. 用户**没写括号** → 沿用老行为：所有 *args（含 pool）按签名 bind_partial
           顺序透传。这条路径主要照顾 `function:converter` / `function:assert_amount_*`
           这类历史函数，它们靠 args[0/1/2] 拿 extra_pool。
        B. 用户**写了括号** → 用户参数为主：foo(*parsed_args)；pool 只有当 foo
           的签名声明 `extra_pool` / `pool` / `**kwargs` 时，才作为关键字参数
           注入（避免 v2 用户写 `function:foo("xxx")` 时把 pool 误当 args[0]
           顶到目标函数的第一个形参）。

    ── 历史坑修复 ──
      旧版本只 strip()，写 `function:foo()` 会查 `foo()` 这个键找不到；
      旧版本没有 args 解析，写 `function:foo("x")` 也只是查 `foo("x")` 找不到。
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

    functions = function_name()

    if f_name not in functions:
        dynamic_names = _dynamic_function_names(project_id)
        available_names = [*dynamic_names, *functions.keys()]
        available = ", ".join(dict.fromkeys(available_names))
        raise ValueError(
            f"未找到指定的函数 '{f_name}'（原始：{raw!r}），可用函数: {available}"
        )

    func = functions[f_name]
    if not callable(func):
        raise TypeError(f"注册的 '{f_name}' 不是可调用对象")

    try:
        sig = inspect.signature(func)
        params = sig.parameters
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )

        if has_parens:
            # 策略 B：用户参数为主，pool 仅在函数显式声明时作为 kwarg 注入
            pool = args[0] if args else None
            call_kwargs = dict(kwargs)
            if pool is not None:
                if "extra_pool" in params or accepts_var_kw:
                    call_kwargs.setdefault("extra_pool", pool)
                if "pool" in params:
                    call_kwargs.setdefault("pool", pool)
            return func(*parsed_args, **call_kwargs)

        # 策略 A：保留老行为，*args 透传（pool 在 args[0]）
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return func(*bound.args, **bound.kwargs)
    except TypeError as e:
        raise TypeError(f"调用 '{f_name}' 参数错误: {e}")
    except Exception as e:
        raise Exception(f"执行 '{f_name}' 时发生错误: {e}")


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


def function_name():
    """
    注册所有可用的函数，作为 exec_func 的函数库
    """
    import pyotp, re, string, time, random

    def google_authentication(secret, *args, **kwargs):
        """
        生成 Google 身份验证器的当前验证码
        """
        if isinstance(secret, list) and secret is not None:
            secret = secret[0]
        return pyotp.TOTP(secret).now()

    def google_authentication_new(*args, **kwargs):
        extra_pool = args[2]
        secret = extra_pool.get('new_secret', '')
        return pyotp.TOTP(secret).now()

    def google_authentication_old(*args, **kwargs):
        extra_pool = args[2]
        secret = extra_pool.get('secret', '')
        return pyotp.TOTP(secret).now()

    def extract_code(text, *args, **kwargs):
        """
        匹配6位数字，前后不能有数字
        """
        if isinstance(text, list) and text is not None:
            match = re.search(r'(?<!\d)\d{6}(?!\d)', text[0])
            clear_cache("sendCode")
            return match.group() if match else None
        else:
            match = re.search(r'(?<!\d)\d{6}(?!\d)', str(text))
            return match.group() if match else None


    def generate_account(*args, **kwargs):
        """
        生成10位随机字母数字字符串，以字母开头
        """
        return ("AU" + str(random.randint(3, 9)) +
                ''.join(random.choice('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
                        for _ in range(5)))

    def generate_num(*args, **kwargs):
        """
        生成19位随机数字字符串
        """
        return ''.join(str(random.randint(0, 9)) for _ in range(6))

    def generate_email(*args, **kwargs):
        """
        生成随机邮箱地址
        """
        letters = string.ascii_lowercase
        domain = ['com', 'net', 'org', 'space']
        username = ''.join(random.choice(letters) for _ in range(8))
        domain_name = ''.join(random.choice(letters) for _ in range(5))
        return f"A_{username}@{domain_name}.{random.choice(domain)}"

    def generate_email_d(*args, **kwargs):
        """
        生成随机邮箱地址
        """
        letters = string.ascii_lowercase
        domain = ['space']
        username = ''.join(random.choice(letters) for _ in range(9))
        domain_name = 'xtec'
        return f"A_{username}@{domain_name}.{random.choice(domain)}"

    def generate_phone(*args, **kwargs):
        """
        生成随机手机号，默认63手机号
        """
        country_code = '852'
        if country_code == '852':
            return random.choice(['9', '6']) + ''.join(str(random.randint(0, 9)) for _ in range(7))
        elif country_code == '886':
            return '9' + ''.join(str(random.randint(0, 9)) for _ in range(9))
        elif country_code == '63':
            prefix = random.choice(['917', '918', '919', '920', '921', '922', '923', '925', '926', '927'])
            return prefix + ''.join(str(random.randint(0, 9)) for _ in range(7))
        else:
            raise ValueError("不支持的国家代码")

    def unique(prefix="AUTO_TEST", *args, **kwargs):
        """
        生成带测试命名空间的唯一字符串，供 AI 接口用例的数据工厂使用。
        """
        clean = re.sub(r"[^0-9A-Za-z_]+", "_", str(prefix or "AUTO_TEST")).strip("_")
        return f"{clean}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

    def unique_mobile(*args, **kwargs):
        """
        生成唯一测试手机号。国内常见 11 位格式，避免复用真实手机号。
        """
        return "199" + "".join(str(random.randint(0, 9)) for _ in range(8))

    def unique_email(*args, **kwargs):
        """
        生成带 AUTO_TEST 命名空间的唯一邮箱。
        """
        return f"auto_test_{int(time.time() * 1000)}_{random.randint(1000, 9999)}@example.test"

    def captcha_solver(*args, **kwargs):
        token = solve_captcha()
        return token

    def converter(*args, **kwargs):
        data = args[1]
        extra_pool = args[2]
        cr = data.get("convertRate", "")
        ea = data.get("exchangeAmount", "")
        dp = extra_pool.get("decimalPrecision", "")
        pi = extra_pool.get("integerPrecision", "")
        pt = extra_pool.get("precisionType", "")
        if not cr and isinstance(cr, str):
            return extra_pool.get("amount_after_convert", "")
        if pt == 2:
            result = str(round(float(ea) * float(cr), int(dp)))
        else:
            result = str(int(float(ea) * float(cr)))
        extra_pool["amount_after_convert"] = result
        return result

    def assert_amount_deduction(*args, **kwargs):
        extra_pool = args[0]
        coin = extra_pool.get("assert_coin", "USDT").upper()
        old_amount = float(extra_pool.get(f"old_amount_{coin}", 0.00))
        amount_set = float(extra_pool.get("amount_set", 0.00))
        result = old_amount - amount_set
        return result

    def assert_amount_increase(*args, **kwargs):
        import math
        def truncate(value: float, digits: int) -> float:
            """截断浮点数到指定小数位，不进位"""
            factor = 10.0 ** digits
            return math.trunc(value * factor) / factor

        extra_pool = args[0]
        coin = extra_pool.get("assert_coin", "USDT").upper()
        old_amount = float(extra_pool.get(f"old_amount_{coin}", 0.00))
        amount_after_convert = float(extra_pool.get("amount_after_convert", 0.00))
        accuracy = int(extra_pool.get("accuracy", 0))
        fee_amount = float(extra_pool.get(f"fee_amount_{coin}", 0.00))
        amount_set = float(extra_pool.get("amount_set", 0.00))

        # deposit=1 withdraw=2 converter=3
        assert_amount_type = extra_pool.get("assert_amount_type", "converter")
        if assert_amount_type == 1:
            result = truncate(old_amount + amount_set - fee_amount, accuracy)
        elif assert_amount_type == 2:
            result = truncate(old_amount + amount_set - fee_amount, accuracy)
        elif assert_amount_type == 3:
            result = truncate(old_amount + amount_after_convert, accuracy)
        else:
            result = old_amount

        return result

    def h5_code(code, *args, **kwargs):
        match = re.search(r'(?<!\d)\d{6}(?!\d)', code)
        return [f'//android.widget.Button[@text="{i}"]' for i in match.group()] if match else []

    def get_timestamp(*args, **kwargs):
        return int(time.time() * 1000)

    # 返回注册的所有函数
    return locals()
