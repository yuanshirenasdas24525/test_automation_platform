"""平台通用动态函数的脚本库种子。

这里只有跨项目通用的数据生成函数。任何项目特有的账号、签名、清理或断言逻辑
都必须由用户在项目脚本库中维护，禁止继续写进 ``utils/function_executor.py``。
"""
from __future__ import annotations

from typing import Any


GLOBAL_SCRIPT_SEEDS: list[dict[str, Any]] = [
    {
        "name": "unique",
        "description": "生成带测试命名空间的唯一字符串",
        "code": '''import random
import re
import time

def handler(prefix="AUTO_TEST", vars=None, ctx=None):
    clean = re.sub(r"[^0-9A-Za-z_]+", "_", str(prefix or "AUTO_TEST")).strip("_")
    return f"{clean}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
''',
    },
    {
        "name": "unique_mobile",
        "description": "生成唯一的测试手机号",
        "code": '''import random

def handler(vars=None, ctx=None):
    return "199" + "".join(str(random.randint(0, 9)) for _ in range(8))
''',
    },
    {
        "name": "unique_email",
        "description": "生成带 AUTO_TEST 命名空间的唯一邮箱",
        "code": '''import random
import time

def handler(vars=None, ctx=None):
    return f"auto_test_{int(time.time() * 1000)}_{random.randint(1000, 9999)}@example.test"
''',
    },
    {
        "name": "get_timestamp",
        "description": "返回当前毫秒时间戳",
        "code": '''import time

def handler(vars=None, ctx=None):
    return int(time.time() * 1000)
''',
    },
    {
        "name": "generate_account",
        "description": "生成以 AU 开头的随机测试账号",
        "code": '''import random
import string

def handler(vars=None, ctx=None):
    return "AU" + str(random.randint(3, 9)) + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(5))
''',
    },
    {
        "name": "generate_num",
        "description": "生成六位随机数字",
        "code": '''import random

def handler(vars=None, ctx=None):
    return "".join(str(random.randint(0, 9)) for _ in range(6))
''',
    },
    {
        "name": "generate_email",
        "description": "生成随机测试邮箱",
        "code": '''import random
import string

def handler(vars=None, ctx=None):
    username = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    domain = "".join(random.choice(string.ascii_lowercase) for _ in range(5))
    return f"A_{username}@{domain}.{random.choice(['com', 'net', 'org', 'space'])}"
''',
    },
    {
        "name": "generate_phone",
        "description": "生成随机测试手机号",
        "code": '''import random

def handler(country_code="852", vars=None, ctx=None):
    code = str(country_code or "852")
    if code == "852":
        return random.choice(["9", "6"]) + "".join(str(random.randint(0, 9)) for _ in range(7))
    if code == "886":
        return "9" + "".join(str(random.randint(0, 9)) for _ in range(9))
    if code == "63":
        prefix = random.choice(["917", "918", "919", "920", "921", "922", "923", "925", "926", "927"])
        return prefix + "".join(str(random.randint(0, 9)) for _ in range(7))
    raise ValueError(f"不支持的国家代码：{code}")
''',
    },
    {
        "name": "extract_code",
        "description": "从文本中提取六位验证码",
        "code": '''import re

def handler(text, vars=None, ctx=None):
    source = text[0] if isinstance(text, list) and text else text
    match = re.search(r"(?<!\\d)\\d{6}(?!\\d)", str(source or ""))
    return match.group() if match else None
''',
    },
    {
        "name": "h5_code",
        "description": "把六位验证码转换成 Android 数字按钮 XPath 列表",
        "code": '''import re

def handler(code, vars=None, ctx=None):
    match = re.search(r"(?<!\\d)\\d{6}(?!\\d)", str(code or ""))
    return [f'//android.widget.Button[@text="{char}"]' for char in match.group()] if match else []
''',
    },
]


def seed_global_scripts(session: Any) -> int:
    """幂等写入缺失的全局函数脚本。"""
    from database.models import ScriptStore

    existing = {
        row[0]
        for row in session.query(ScriptStore.name).filter(
            ScriptStore.project_id.is_(None),
            ScriptStore.kind == "function",
        ).all()
    }
    added = 0
    for item in GLOBAL_SCRIPT_SEEDS:
        if item["name"] in existing:
            continue
        session.add(ScriptStore(
            name=item["name"],
            kind="function",
            code=item["code"],
            enabled=True,
            project_id=None,
            description=item["description"],
            requirements=[],
        ))
        added += 1
    return added
