# -*- coding:utf-8 -*-
import inspect

from src.utils.logger import LOGGER
from src.utils.platform_utils import execution_time_decorator
from src.captcha_solver import solve_captcha

@execution_time_decorator
def exec_func(value, *args, **kwargs):
    """
    通用函数执行器，根据 "function:" 前缀调用注册中心函数
    :param value: 可能包含 "function:" 前缀的字符串
    :param args: 位置参数（传给目标函数）
    :param kwargs: 关键字参数（传给目标函数）
    """
    if not (isinstance(value, str) and value.startswith("function:")):
        return value

    f_name = value.split("function:", 1)[1].strip()
    functions = function_name()

    if f_name not in functions:
        available = ", ".join(functions.keys())
        raise ValueError(f"未找到指定的函数 '{f_name}'，可用函数: {available}")

    func = functions[f_name]
    if not callable(func):
        raise TypeError(f"注册的 '{f_name}' 不是可调用对象")

    try:
        sig = inspect.signature(func)
        # 根据签名匹配参数数量
        bound_args = sig.bind_partial(*args, **kwargs)
        bound_args.apply_defaults()
        return func(*bound_args.args, **bound_args.kwargs)
    except TypeError as e:
        raise TypeError(f"调用 '{f_name}' 参数错误: {e}")
    except Exception as e:
        raise Exception(f"执行 '{f_name}' 时发生错误: {e}")

def function_name():
    """
    注册所有可用的函数，作为 exec_func 的函数库
    """
    import pyotp, re, string, time, random
    from decimal import Decimal, getcontext, ROUND_DOWN, InvalidOperation

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
                        for _ in range(7)))

    def generate_num(*args, **kwargs):
        """
        生成19位随机数字字符串
        """
        return ''.join(str(random.randint(0, 9)) for _ in range(19))

    def generate_email(*args, **kwargs):
        """
        生成随机邮箱地址
        """
        letters = string.ascii_lowercase
        domain = ['com', 'net', 'org']
        username = ''.join(random.choice(letters) for _ in range(8))
        domain_name = ''.join(random.choice(letters) for _ in range(5))
        return f"A_{username}@{domain_name}.{random.choice(domain)}"

    def generate_phone(country_code='63', *args, **kwargs):
        """
        生成随机手机号，默认63手机号
        """
        if country_code == '852':
            return random.choice(['9', '6']) + ''.join(str(random.randint(0, 9)) for _ in range(7))
        elif country_code == '886':
            return '9' + ''.join(str(random.randint(0, 9)) for _ in range(9))
        elif country_code == '63':
            prefix = random.choice(['917', '918', '919', '920', '921', '922', '923', '925', '926', '927'])
            return prefix + ''.join(str(random.randint(0, 9)) for _ in range(7))
        else:
            raise ValueError("不支持的国家代码")

    def captcha_solver(*args, **kwargs):
        token = solve_captcha()
        return token

    def converter(*args, **kwargs):
        data = args[1]
        extra_pool = args[2]
        cr = data.get("convertRate", "")
        ea = data.get("exchangeAmount", "")
        dp = args[2].get("decimalPrecision", "")
        if not cr and isinstance(cr, str):
            return extra_pool.get("amount_after_convert", "")
        if dp:
            result = str(round(float(ea) * float(cr), int(dp)))
        else:
            result = str(int(ea * cr))
        extra_pool["amount_after_convert"] = result
        return result

    def h5_code(code, *args, **kwargs):
        match = re.search(r'(?<!\d)\d{6}(?!\d)', code)
        return [f'//android.widget.Button[@text="{i}"]' for i in match.group()] if match else []

    def get_timestamp(*args, **kwargs):
        return int(time.time() * 1000)

    # 返回注册的所有函数
    return locals()