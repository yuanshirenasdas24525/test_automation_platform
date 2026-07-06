# -*- coding:utf-8 -*-
"""配置中心『推荐配置项』schema 注册表。

为什么这样改：
  老方案是在 FastAPI 启动时把 ini 五节"系统配置" seed 进 config_store，加一个
  禁删标记。结果用户体验割裂——明明界面让"新增"，但若不主动加，那些键又"凭空"出现。
  改回跟 web 一致的「推荐项面板」模式：所有键都不强制写库；推荐什么键、默认值多少、
  类型、示例 …… 都从 schema 里读，前端 RecommendedConfigPanel 拉出来供用户『一键填入』。
  用户决定到底要不要加，加完也能正常删除——心智模型清晰，不再有任何"系统级保护项"。

每个 schema 条目（一个 dict）字段约定（跟 WEB_CONFIG_SCHEMA 同形）：
  - key            : config_key 列的值（`config.key`）
  - config_group   : 归属 group（`config.group`）
  - type           : "str" | "bool" | "int" | "float" | "json"，仅做前端类型提示
  - default        : 默认值字符串
  - description    : 给前端展示的一句话说明
  - example        : 示例值
  - applies_to     : 适用场景列表（自由文本，给前端做小标签提示）

注意：这里不直接进 DB —— 真正落库还是走 /api/config/save。这只是『推荐』。
"""
from __future__ import annotations

from typing import Any


# =============================================================================
# API 自动化推荐配置项
# 5 节经典配置（host / mysql_db / redis / default_parameters / encryption_decryption）
# + 默认请求头（推荐配置请求头）。用户『一键填入』后到下方表单即可保存。
# =============================================================================
API_CONFIG_SCHEMA: list[dict[str, Any]] = [
    # —— 1. host：接口测试主机 ——
    {
        "config_group": "host",
        "key": "url",
        "type": "str",
        "default": "https://example.com",
        "description": "接口测试主机基础 URL。RequestDataProcessor 会把相对路径拼到这里。",
        "example": "https://api.example.com",
        "applies_to": ["api"],
    },

    # —— 2. target_db：默认数据库连接（SQL 步骤 / extract_data 走这里） ——
    {
        "config_group": "target_db",
        "key": "type",
        "type": "str",
        "default": "mysql",
        "description": "数据库类型（mysql / postgresql / sqlite）。SQL 步骤 / extract_data 走这里取连接。",
        "example": "mysql",
        "applies_to": ["api"],
    },
    {
        "config_group": "target_db",
        "key": "host",
        "type": "str",
        "default": "127.0.0.1",
        "description": "数据库主机。",
        "example": "127.0.0.1",
        "applies_to": ["api"],
    },
    {
        "config_group": "target_db",
        "key": "port",
        "type": "int",
        "default": "3306",
        "description": "数据库端口。",
        "example": "3306",
        "applies_to": ["api"],
    },
    {
        "config_group": "target_db",
        "key": "user",
        "type": "str",
        "default": "",
        "description": "数据库账号。",
        "example": "root",
        "applies_to": ["api"],
    },
    {
        "config_group": "target_db",
        "key": "password",
        "type": "str",
        "default": "",
        "description": "数据库密码。",
        "example": "******",
        "applies_to": ["api"],
    },
    {
        "config_group": "target_db",
        "key": "database",
        "type": "str",
        "default": "",
        "description": "目标库名。",
        "example": "test_db",
        "applies_to": ["api"],
    },

    # —— 3. redis ——
    {
        "config_group": "redis",
        "key": "host",
        "type": "str",
        "default": "127.0.0.1",
        "description": "Redis 主机。",
        "example": "127.0.0.1",
        "applies_to": ["api"],
    },
    {
        "config_group": "redis",
        "key": "port",
        "type": "int",
        "default": "6379",
        "description": "Redis 端口。",
        "example": "6379",
        "applies_to": ["api"],
    },
    {
        "config_group": "redis",
        "key": "db",
        "type": "int",
        "default": "0",
        "description": "Redis 数据库编号。",
        "example": "0",
        "applies_to": ["api"],
    },
    {
        "config_group": "redis",
        "key": "password",
        "type": "str",
        "default": "",
        "description": "Redis 密码。无密码留空。",
        "example": "",
        "applies_to": ["api"],
    },

    # —— 4. default_parameters：业务默认参数（${var} 替换池） ——
    {
        "config_group": "default_parameters",
        "key": "mobile",
        "type": "str",
        "default": "",
        "description": "默认手机号。在用例 ${mobile} 处会被替换。",
        "example": "13800000000",
        "applies_to": ["api"],
    },
    {
        "config_group": "default_parameters",
        "key": "my_account",
        "type": "str",
        "default": "",
        "description": "默认账号。${my_account} 处会被替换。",
        "example": "qa_user",
        "applies_to": ["api"],
    },
    {
        "config_group": "default_parameters",
        "key": "my_password",
        "type": "str",
        "default": "",
        "description": "默认密码。${my_password} 处会被替换。",
        "example": "Qa@123456",
        "applies_to": ["api"],
    },

    # —— 5. encryption_decryption：加解密 ——
    {
        "config_group": "encryption_decryption",
        "key": "on_off",
        "type": "bool",
        "default": "false",
        "description": "是否启用加解密钩子。on/true 才生效。",
        "example": "false",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "key",
        "type": "str",
        "default": "",
        "description": "对称加密密钥（建议 32 位 AES）。",
        "example": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "encrypt",
        "type": "json",
        "default": "[]",
        "description": "请求体中需要逐字段 AES-GCM 加密的字段名列表（JSON 数组，支持 data.user.id 形式）。",
        "example": '["password", "id_card"]',
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "decrypt",
        "type": "json",
        "default": "[]",
        "description": "响应里需要解密的字段名列表（JSON 数组）。",
        "example": '["data.password"]',
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "request_header_order_encrypt",
        "type": "json",
        "default": "[]",
        "description": "请求头签名字段顺序；按数组顺序拼接 k=v 生成 power-sign。为空不启用。",
        "example": '["X-App-Id", "X-Timestamp", "Authorization"]',
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "request_body_order_encrypt",
        "type": "json",
        "default": "[]",
        "description": "请求体签名字段顺序；按数组顺序拼接 k=v 生成 power-sign。旧配置只开 on_off 时仍按请求体 key 升序签名。",
        "example": '["username", "password", "nonce"]',
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "request_header_encrypt",
        "type": "json",
        "default": "[]",
        "description": "请求头中需要逐字段 AES-GCM 加密的字段名列表。",
        "example": '["X-Device-Id"]',
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "request_body_whole_encrypt",
        "type": "bool",
        "default": "false",
        "description": "是否启用请求体整体 AES-GCM 加密。",
        "example": "false",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "request_body_whole_encrypt_field",
        "type": "str",
        "default": "data",
        "description": "请求体整体加密后的承载字段；填 raw 或留空表示直接把请求体替换为密文字符串。",
        "example": "data",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "response_body_whole_decrypt",
        "type": "bool",
        "default": "false",
        "description": "是否在断言/提取前对响应体整体 AES-GCM 解密。",
        "example": "false",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "response_body_whole_decrypt_field",
        "type": "str",
        "default": "",
        "description": "响应密文字段路径；为空表示整个响应就是密文字符串，支持 data 这类字段路径。",
        "example": "data",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "custom_crypto_module",
        "type": "str",
        "default": "utils.custom_crypto",
        "description": "自定义加解密 Python 模块路径。默认使用 utils/custom_crypto.py。",
        "example": "utils.custom_crypto",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "custom_request_handler",
        "type": "str",
        "default": "",
        "description": "请求发送前调用的自定义函数名，函数签名为 fn(headers, body, config)。",
        "example": "custom_request_crypto",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "custom_response_handler",
        "type": "str",
        "default": "",
        "description": "响应断言/提取前调用的自定义函数名，函数签名为 fn(response_body, config)。",
        "example": "custom_response_crypto",
        "applies_to": ["api"],
    },
    {
        "config_group": "encryption_decryption",
        "key": "custom_crypto_only",
        "type": "bool",
        "default": "false",
        "description": "是否只执行自定义加解密函数；false 时自定义函数后仍继续执行内置签名/AES 配置。",
        "example": "true",
        "applies_to": ["api"],
    },

    # —— 6. headers：推荐请求头 ——
    # 接口用例的 case-level headers 通常会覆盖这里；这里给『全局兜底』和『一键填入』
    # 提供模板。常见的 Content-Type / Authorization / User-Agent 这三件套先放出来。
    {
        "config_group": "headers",
        "key": "Content-Type",
        "type": "str",
        "default": "application/json",
        "description": "默认请求体类型。POST/PUT 接口最常见的就是这个。",
        "example": "application/json",
        "applies_to": ["api"],
    },
    {
        "config_group": "headers",
        "key": "Authorization",
        "type": "str",
        "default": "",
        "description": "默认鉴权头。常见格式：'Bearer xxx' 或 'Basic xxx'。",
        "example": "Bearer ${token}",
        "applies_to": ["api"],
    },
    {
        "config_group": "headers",
        "key": "User-Agent",
        "type": "str",
        "default": "",
        "description": "默认 User-Agent。留空则用 requests 默认。",
        "example": "QA-Platform/1.0",
        "applies_to": ["api"],
    },
    {
        "config_group": "headers",
        "key": "Accept",
        "type": "str",
        "default": "application/json",
        "description": "默认 Accept。多数接口返回 JSON 时显式声明可避免 406。",
        "example": "application/json",
        "applies_to": ["api"],
    },
    {
        "config_group": "headers",
        "key": "X-Request-Id",
        "type": "str",
        "default": "",
        "description": "请求追踪 ID。配合 ${uuid()} function 可以每条请求生成不同 ID。",
        "example": "function:uuid()",
        "applies_to": ["api"],
    },
]


# =============================================================================
# App 自动化推荐配置项
# 重点：黑名单（哪些 udid / bundleId 不参与自动化）+ 几个常用的执行期开关。
# =============================================================================
APP_CONFIG_SCHEMA: list[dict[str, Any]] = [
    # —— 黑名单 ——
    {
        "config_group": "blacklist",
        "key": "udids",
        "type": "json",
        "default": "[]",
        "description": (
            "设备黑名单。列在这里的 udid 即使 status=idle 也不会被 DevicePool 选中。"
            "比如有台手机平时给开发自测用，不希望被自动化跑残。"
        ),
        "example": '["emulator-5556", "abcd1234"]',
        "applies_to": ["app"],
    },
    {
        "config_group": "blacklist",
        "key": "app_packages",
        "type": "json",
        "default": "[]",
        "description": (
            "应用黑名单（Android）。包名出现在这里的 app_install / app_launch 步骤会被拒绝执行，"
            "防止误把生产/真机里的关键应用清掉。"
        ),
        "example": '["com.android.systemui", "com.bank.production"]',
        "applies_to": ["app"],
    },
    {
        "config_group": "blacklist",
        "key": "bundle_ids",
        "type": "json",
        "default": "[]",
        "description": (
            "应用黑名单（iOS）。bundleId 出现在这里的 app_install / app_launch 步骤会被拒绝。"
        ),
        "example": '["com.apple.AppStore"]',
        "applies_to": ["app"],
    },

    # —— 默认执行期开关 ——
    {
        "config_group": "session",
        "key": "reuse_across_cases",
        "type": "bool",
        "default": "true",
        "description": "App 会话是否跨用例复用（true 性能好，false 每条 case 重新拉 driver）。",
        "example": "true",
        "applies_to": ["app"],
    },
    {
        "config_group": "session",
        "key": "default_timeout",
        "type": "int",
        "default": "10",
        "description": "查找元素 / 等待元素的默认超时（秒）。",
        "example": "10",
        "applies_to": ["app"],
    },
    {
        "config_group": "session",
        "key": "screenshot_on_failure",
        "type": "bool",
        "default": "true",
        "description": "step 失败时是否自动截图并塞进 Allure（也可在 step.config 里覆盖）。",
        "example": "true",
        "applies_to": ["app"],
    },

    # —— 心跳探测节奏 ——
    {
        "config_group": "probe",
        "key": "appium_timeout",
        "type": "float",
        "default": "2.0",
        "description": "Appium /status 探测的 HTTP 超时（秒）。",
        "example": "2.0",
        "applies_to": ["app"],
    },
    {
        "config_group": "probe",
        "key": "offline_threshold",
        "type": "int",
        "default": "2",
        "description": "连续探测失败多少次后把设备置为 offline。1 = 一次失败就翻 offline。",
        "example": "2",
        "applies_to": ["app"],
    },
]


# =============================================================================
# AI provider 配置（category="ai"）
# =============================================================================
AI_CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "config_group": "provider",
        "key": "provider",
        "type": "string",
        "default": "openai",
        "description": "LLM 提供商。当前支持 openai / zai / anthropic / ollama（其它走自建反代时用 custom）。",
        "example": "openai",
        "applies_to": ["ai"],
    },
    {
        "config_group": "provider",
        "key": "api_key",
        "type": "string",
        "default": "",
        "description": "对应 provider 的 API Key。生产环境强烈建议从环境变量注入，不要明文存这里。",
        "example": "sk-xxx...",
        "applies_to": ["ai"],
    },
    {
        "config_group": "provider",
        "key": "model",
        "type": "string",
        "default": "gpt-4o-mini",
        "description": "默认 model 名。OpenAI: gpt-4o / gpt-4o-mini；Anthropic: claude-3-5-sonnet-20241022 / claude-3-5-haiku-20241022。",
        "example": "gpt-4o-mini",
        "applies_to": ["ai"],
    },
    {
        "config_group": "provider",
        "key": "base_url",
        "type": "string",
        "default": "",
        "description": "自定义 endpoint（自建反代 / Azure OpenAI 用）。留空走官方默认。",
        "example": "https://api.openai.com",
        "applies_to": ["ai"],
    },
    {
        "config_group": "provider",
        "key": "max_tokens",
        "type": "int",
        "default": "4096",
        "description": "单次响应最大 token 上限。",
        "example": "4096",
        "applies_to": ["ai"],
    },
]


def get_schema(category: str) -> list[dict[str, Any]]:
    """按 category 取推荐 schema。未识别 category 返回空列表。

    web 走 runners.web.session.WEB_CONFIG_SCHEMA（在那边定义好了），所以这里
    遇到 web 时会做一次 lazy import 转发——避免在没装 playwright 的纯 API 模式下报错。
    """
    cat = (category or "").strip().lower()
    if cat == "api":
        return [dict(item) for item in API_CONFIG_SCHEMA]
    if cat == "app":
        return [dict(item) for item in APP_CONFIG_SCHEMA]
    if cat == "ai":
        return [dict(item) for item in AI_CONFIG_SCHEMA]
    if cat == "web":
        try:
            from runners.web.session import WEB_CONFIG_SCHEMA  # noqa: WPS433
        except Exception:  # noqa: BLE001
            return []
        # web schema 没带 config_group 字段（它的隐式 group 是 'browser'），统一补上
        return [{"config_group": "browser", **dict(item)} for item in WEB_CONFIG_SCHEMA]
    if cat == "other":
        return OTHER_CONFIG_SCHEMA
    return []


# ---------------------------------------------------------------------------
# 其他配置 —— 自由 key-value，不限定 config_group
# ---------------------------------------------------------------------------
OTHER_CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "config_group": "git",
        "key": "git_url",
        "type": "string",
        "default": "",
        "description": "Git 仓库地址（HTTPS 或 SSH 格式）。",
        "example": "git@github.com:user/repo.git",
        "applies_to": ["other"],
    },
    {
        "config_group": "git",
        "key": "git_default_branch",
        "type": "string",
        "default": "main",
        "description": "默认分支名。",
        "example": "main",
        "applies_to": ["other"],
    },
    {
        "config_group": "git",
        "key": "git_auth_type",
        "type": "string",
        "default": "ssh_key",
        "description": "认证方式：pat（Personal Access Token）或 ssh_key。",
        "example": "ssh_key",
        "applies_to": ["other"],
    },
    {
        "config_group": "git",
        "key": "git_auth_secret",
        "type": "string",
        "default": "",
        "description": "认证密钥：PAT token 或 SSH 私钥内容。",
        "example": "-----BEGIN OPENSSH PRIVATE KEY-----",
        "applies_to": ["other"],
    },
]
