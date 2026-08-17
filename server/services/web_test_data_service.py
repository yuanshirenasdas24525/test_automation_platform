"""Web UI 自动化测试数据与账号生命周期。

AI 只描述“需要什么账号”，真实凭据在运行前绑定：

* 无账号场景（空值、用户不存在）使用本地生成值；
* 正常、停用、边界、锁定账号由项目配置的 HTTP 或脚本账号工厂临时创建；
* 内置管理员等共享账号从项目级加密配置读取；
* 无法由单浏览器稳定准备的多来源、等待锁定过期场景明确阻断。

这里不依赖目标系统的用户 ORM。简单项目可配置登录、创建、清理 HTTP 接口；需要
特殊签名、多接口编排或其它项目逻辑时，改用脚本库 workflow。平台只负责 JSON 调度。

测试账号配置存放在 ``config_store`` 的 ``web/test_accounts`` 组。密码值使用
``utils.crypto`` 加密，查询接口只返回掩码。
"""
from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any
from urllib.parse import urljoin

import requests
from jsonpath_ng.ext import parse as parse_jsonpath
from sqlalchemy.orm import Session

from database.models import ConfigStore, ScriptStore
from database.models.script_store import SCRIPT_KIND_WORKFLOW
from utils.crypto import decrypt_secret, encrypt_secret
from utils.script_runtime import run_named_script


TEST_ACCOUNT_CONFIG_GROUP = "test_accounts"
TEST_ACCOUNT_PROVIDER_HTTP = "http"
TEST_ACCOUNT_PROVIDER_SCRIPT = "script"
# 兼容已经保存的旧配置；实际执行也改走 HTTP，不再访问 User/Role ORM。
TEST_ACCOUNT_PROVIDER_SELF = "self_platform"
TEST_ACCOUNT_PROVIDER_STATIC = "static"
TEST_ACCOUNT_SECRET_PREFIX = "enc:v1:"
TEST_ACCOUNT_SECRET_MASK = "••••••••"
TEST_ACCOUNT_USER_PREFIX = "AUTO_UI_"
TEST_ACCOUNT_FULL_NAME = "Web UI 自动化临时账号"

DEFAULT_HTTP_LOGIN_PATH = "/api/auth/login"
DEFAULT_HTTP_LOGIN_BODY = (
    '{"username":"${shared_username}","password":"${shared_password}",'
    '"client":{"client_type":"automation","client_name":"Web UI Account Factory",'
    '"device_id":"web-test-account-factory"}}'
)
DEFAULT_HTTP_TOKEN_JSONPATH = "$.data.access_token"
DEFAULT_HTTP_CREATE_PATH = "/api/users"
DEFAULT_HTTP_CREATE_BODY = (
    '{"username":"${username}","full_name":"${account_full_name}",'
    '"password":"${password}","is_active":"${is_active}","role_codes":["test"]}'
)
DEFAULT_HTTP_USER_ID_JSONPATH = "$.data.id"
DEFAULT_HTTP_CLEANUP_METHOD = "DELETE"
DEFAULT_HTTP_CLEANUP_PATH = "/api/users/${user_id}/purge-test-account"

_SECRET_KEYS = {"shared_password"}
_USERNAME_MARKERS = ("username", "user_name", "account", "用户名", "账号")
_PASSWORD_MARKERS = ("password", "passwd", "pwd", "密码")


class WebTestDataError(ValueError):
    """测试数据无法安全准备。"""


def is_test_account_secret(group: str | None, key: str | None) -> bool:
    """判断配置项是否为测试账号密钥。"""
    return (
        str(group or "").strip().lower() == TEST_ACCOUNT_CONFIG_GROUP
        and str(key or "").strip().lower() in _SECRET_KEYS
    )


def encode_test_account_secret(value: str) -> str:
    """把测试账号密码加密成可识别的配置值。"""
    text = str(value or "")
    if text.startswith(TEST_ACCOUNT_SECRET_PREFIX):
        return text
    return TEST_ACCOUNT_SECRET_PREFIX + encrypt_secret(text)


def decode_test_account_secret(value: str | None) -> str:
    """解密测试账号密码；兼容尚未迁移的明文值。"""
    text = str(value or "")
    if not text:
        return ""
    if text.startswith(TEST_ACCOUNT_SECRET_PREFIX):
        return decrypt_secret(text[len(TEST_ACCOUNT_SECRET_PREFIX):])
    return text


def mask_test_account_config(group: str | None, key: str | None, value: Any) -> Any:
    """配置查询响应脱敏。"""
    if is_test_account_secret(group, key) and value:
        return TEST_ACCOUNT_SECRET_MASK
    return value


def prepare_test_account_config_value(
    group: str | None,
    key: str | None,
    value: str | None,
    *,
    existing: str | None = None,
) -> str | None:
    """配置写入前处理密钥；编辑时空值/掩码表示保留原密码。"""
    if not is_test_account_secret(group, key):
        return value
    text = str(value or "")
    if text in {"", TEST_ACCOUNT_SECRET_MASK}:
        return existing
    return encode_test_account_secret(text)


def _variable_key(variables: dict[str, Any], markers: tuple[str, ...], fallback: str) -> str:
    for key in variables:
        lowered = str(key).lower()
        if any(marker in lowered for marker in markers):
            return str(key)
    return fallback


def infer_account_requirement(
    title: str | None,
    description: str | None = None,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从用例意图推导账号能力，不推导真实凭据。

    这是生成器和历史回填共用的确定性门禁。AI 即使漏写测试数据声明，也不能让
    密码为空的“成功登录”脚本被误判成可执行。
    """
    values = dict(variables or {})
    text = f"{title or ''} {description or ''}".lower()
    username_variable = _variable_key(values, _USERNAME_MARKERS, "username")
    password_variable = _variable_key(values, _PASSWORD_MARKERS, "password")
    base = {
        "version": 1,
        "status": "ready",
        "profile": "none",
        "credential_mode": "none",
        "username_variable": username_variable,
        "password_variable": password_variable,
        "lifecycle": "none",
        "reason": "该场景不需要真实账号",
    }

    if any(marker in text for marker in ("页面加载", "页加载", "页面展示")) or not values:
        return base
    if "用户名和密码均为空" in text or "用户名密码均为空" in text:
        return {**base, "profile": "form_empty", "credential_mode": "both_empty"}
    if "用户名为空" in text:
        return {**base, "profile": "form_empty", "credential_mode": "empty_username"}
    if "密码为空" in text:
        return {**base, "profile": "form_empty", "credential_mode": "empty_password"}
    if "不存在的用户名" in text or "用户不存在" in text:
        return {
            **base,
            "profile": "synthetic_nonexistent",
            "credential_mode": "wrong",
            "reason": "使用每次运行唯一且确定不存在的用户名",
        }

    contract_mismatch = None
    if re.search(r"密码长度\s*5\s*位", text) or "密码长度5位" in text:
        contract_mismatch = "登录接口只要求密码非空；密码最小6位属于创建用户规则"
    elif "用户名包含非法字符" in text:
        contract_mismatch = "登录接口不校验用户名字符集；字符规则属于创建用户接口"
    elif re.search(r"用户名长度\s*65\s*位", text) or "用户名长度65位" in text:
        contract_mismatch = "当前页面没有与接口422错误文案一致的可验证预期"
    if contract_mismatch:
        return {
            **base,
            "status": "contract_mismatch",
            "profile": "unsupported",
            "reason": contract_mismatch,
        }

    if any(marker in text for marker in ("锁定时间过后", "其他来源", "任何来源")):
        return {
            **base,
            "status": "unsupported",
            "profile": "isolated_lock_account",
            "lifecycle": "dynamic",
            "reason": "需要可控时钟或多浏览器来源，不能由当前单浏览器稳定准备",
        }
    if "锁定期间" in text:
        return {
            **base,
            "status": "unsupported",
            "profile": "isolated_lock_account",
            "lifecycle": "dynamic",
            "reason": "需要先在同一来源准备锁定状态，当前脚本缺少可靠前置步骤",
        }
    if "连续5次" in text or "连续 5 次" in text:
        return {
            **base,
            "profile": "isolated_lock_account",
            "credential_mode": "wrong",
            "lifecycle": "dynamic",
            "reason": "每次运行创建独立账号，避免锁定共享账号",
        }
    if "已停用账号" in text or "停用账号" in text:
        return {
            **base,
            "profile": "dynamic_disabled",
            "credential_mode": "correct",
            "lifecycle": "dynamic",
            "reason": "运行前创建并停用专用账号",
        }
    if "密码错误" in text or "登录失败后" in text:
        return {
            **base,
            "profile": "dynamic_active",
            "credential_mode": "wrong",
            "lifecycle": "dynamic",
            "reason": "使用独立普通账号和错误密码，避免锁定共享管理员",
        }
    if "admin" in text and ("成功" in text or "默认密码" in text):
        return {
            **base,
            "profile": "shared_admin",
            "credential_mode": "correct",
            "lifecycle": "shared",
            "reason": "绑定项目级加密共享管理员账号",
        }

    profile = "dynamic_active"
    constraints: dict[str, Any] = {}
    if re.search(r"用户名长度\s*64\s*位", text) or "用户名长度64位" in text:
        profile = "dynamic_boundary"
        constraints["username_length"] = 64
    elif "允许的特殊字符" in text:
        profile = "dynamic_boundary"
        constraints["username_allowed_special"] = True
    elif re.search(r"密码长度\s*6\s*位", text) or "密码长度6位" in text:
        profile = "dynamic_boundary"
        constraints["password_length"] = 6
    elif re.search(r"密码长度\s*128\s*位", text) or "密码长度128位" in text:
        profile = "dynamic_boundary"
        constraints["password_length"] = 128

    return {
        **base,
        "profile": profile,
        "credential_mode": "correct",
        "lifecycle": "dynamic",
        "constraints": constraints,
        "reason": "运行前创建满足条件的独立测试账号",
    }


def load_test_account_config(session: Session, project_id: int) -> dict[str, Any]:
    """读取项目账号工厂配置，并兼容既有管理员凭据。"""
    rows = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.project_id == project_id,
            ConfigStore.category == "web",
            ConfigStore.config_group == TEST_ACCOUNT_CONFIG_GROUP,
        )
        .all()
    )
    config = {str(row.config_key): row.config_value for row in rows}
    password = decode_test_account_secret(config.get("shared_password"))
    username = str(config.get("shared_username") or "").strip()

    browser_rows = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.project_id == project_id,
            ConfigStore.category == "web",
            ConfigStore.config_group == "browser",
            ConfigStore.config_key == "base_url",
        )
        .all()
    )
    browser_base_url = next(
        (str(row.config_value or "").strip() for row in browser_rows if row.config_value),
        "",
    )
    api_host_rows = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.project_id == project_id,
            ConfigStore.category == "api",
            ConfigStore.config_group == "host",
            ConfigStore.config_key == "url",
        )
        .all()
    )
    api_host_url = next(
        (str(row.config_value or "").strip() for row in api_host_rows if row.config_value),
        "",
    )

    if not username or not password:
        legacy_rows = (
            session.query(ConfigStore)
            .filter(
                ConfigStore.project_id == project_id,
                ConfigStore.category == "api",
                ConfigStore.config_group == "default_parameters",
                ConfigStore.config_key.in_(["user_admin", "password_admin"]),
            )
            .all()
        )
        legacy = {str(row.config_key): str(row.config_value or "") for row in legacy_rows}
        username = username or legacy.get("user_admin", "")
        password = password or legacy.get("password_admin", "")

    provider = str(config.get("provider") or TEST_ACCOUNT_PROVIDER_STATIC).strip().lower()
    if provider == TEST_ACCOUNT_PROVIDER_SELF:
        provider = TEST_ACCOUNT_PROVIDER_HTTP

    return {
        "provider": provider,
        "shared_username": username,
        "shared_password": password,
        "auto_cleanup": str(config.get("auto_cleanup") or "true").strip().lower()
        not in {"0", "false", "no", "off"},
        "prepare_script": str(config.get("prepare_script") or "").strip(),
        "cleanup_script": str(config.get("cleanup_script") or "").strip(),
        "script_config": config.get("script_config") or {},
        "api_base_url": str(
            config.get("api_base_url")
            or os.getenv("WEB_TEST_ACCOUNT_API_BASE_URL")
            or browser_base_url
            or api_host_url
        ).strip(),
        "login_method": str(config.get("login_method") or "POST").strip().upper(),
        "login_path": str(
            DEFAULT_HTTP_LOGIN_PATH
            if "login_path" not in config
            else config.get("login_path") or ""
        ).strip(),
        "login_body": config.get("login_body") or DEFAULT_HTTP_LOGIN_BODY,
        "token_jsonpath": str(
            DEFAULT_HTTP_TOKEN_JSONPATH
            if "token_jsonpath" not in config
            else config.get("token_jsonpath") or ""
        ).strip(),
        "auth_header": str(
            "Authorization"
            if "auth_header" not in config
            else config.get("auth_header") or ""
        ).strip(),
        "auth_scheme": str(
            "Bearer" if "auth_scheme" not in config else config.get("auth_scheme") or ""
        ).strip(),
        "create_method": str(config.get("create_method") or "POST").strip().upper(),
        "create_path": str(config.get("create_path") or DEFAULT_HTTP_CREATE_PATH).strip(),
        "create_body": config.get("create_body") or DEFAULT_HTTP_CREATE_BODY,
        "user_id_jsonpath": str(
            config.get("user_id_jsonpath") or DEFAULT_HTTP_USER_ID_JSONPATH
        ).strip(),
        "cleanup_method": str(
            config.get("cleanup_method") or DEFAULT_HTTP_CLEANUP_METHOD
        ).strip().upper(),
        "cleanup_path": str(
            config.get("cleanup_path") or DEFAULT_HTTP_CLEANUP_PATH
        ).strip(),
        "timeout_seconds": _positive_float(config.get("timeout_seconds"), default=10.0),
    }


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _parse_json_template(value: Any, *, key: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise WebTestDataError(f"test_accounts.{key} 不是合法 JSON：{exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise WebTestDataError(f"test_accounts.{key} 必须是 JSON 对象")
    return parsed


_TEMPLATE_VALUE_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}$")
_TEMPLATE_INLINE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


def _render_template(value: Any, context: dict[str, Any]) -> Any:
    """递归渲染 HTTP 模板；整值占位符保留 bool/int/list 等原始类型。"""
    if isinstance(value, dict):
        return {str(key): _render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    if not isinstance(value, str):
        return value
    full_match = _TEMPLATE_VALUE_RE.fullmatch(value)
    if full_match:
        return context.get(full_match.group(1), "")
    return _TEMPLATE_INLINE_RE.sub(
        lambda match: str(context.get(match.group(1), "")),
        value,
    )


def _extract_jsonpath(payload: Any, expression: str, *, field: str) -> Any:
    try:
        matches = parse_jsonpath(expression).find(payload)
    except Exception as exc:  # noqa: BLE001
        raise WebTestDataError(
            f"test_accounts.{field} 不是合法 JSONPath：{expression}"
        ) from exc
    if not matches:
        raise WebTestDataError(
            f"账号工厂响应未命中 {field}={expression}"
        )
    return matches[0].value


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.text or "").strip()[:300]
    if isinstance(payload, dict):
        for key in ("detail", "message", "error", "msg"):
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)):
                return str(value)[:300]
    return f"响应结构：{type(payload).__name__}"


class _HttpTestAccountClient:
    """按项目配置调用目标系统账号 API；不感知目标系统数据库模型。"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.session = requests.Session()
        self._access_token: str | None = None

    def close(self) -> None:
        self.session.close()

    def _url(self, path: str) -> str:
        if re.match(r"^https?://", path, re.IGNORECASE):
            return path
        base_url = str(self.config.get("api_base_url") or "").strip()
        if not base_url:
            raise WebTestDataError(
                "HTTP 账号工厂缺少 test_accounts.api_base_url，"
                "也没有可复用的 web/browser.base_url"
            )
        return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        url = self._url(path)
        try:
            response = self.session.request(
                method=method,
                url=url,
                json=body,
                headers=headers,
                timeout=float(self.config.get("timeout_seconds") or 10.0),
            )
        except requests.RequestException as exc:
            raise WebTestDataError(
                f"账号工厂接口不可连接：{method} {url}；{type(exc).__name__}"
            ) from exc
        if allow_not_found and response.status_code == 404:
            return {}
        if not 200 <= response.status_code < 300:
            reason = _response_error(response)
            raise WebTestDataError(
                f"账号工厂接口失败：{method} {url}；HTTP {response.status_code}"
                + (f"；{reason}" if reason else "")
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise WebTestDataError(
                f"账号工厂接口没有返回 JSON：{method} {url}"
            ) from exc

    def _auth_headers(self) -> dict[str, str]:
        login_path = str(self.config.get("login_path") or "").strip()
        if not login_path:
            return {}
        if self._access_token is None:
            template = _parse_json_template(
                self.config.get("login_body"),
                key="login_body",
            )
            body = _render_template(template, {
                "shared_username": self.config.get("shared_username") or "",
                "shared_password": self.config.get("shared_password") or "",
            })
            payload = self._request(
                str(self.config.get("login_method") or "POST"),
                login_path,
                body=body,
            )
            token_path = str(self.config.get("token_jsonpath") or "").strip()
            if token_path:
                token = _extract_jsonpath(
                    payload,
                    token_path,
                    field="token_jsonpath",
                )
                self._access_token = str(token)
            else:
                # Cookie 登录由 requests.Session 自动保持；空串作为“已登录”标记。
                self._access_token = ""
        header = str(self.config.get("auth_header") or "")
        scheme = str(self.config.get("auth_scheme") or "Bearer").strip()
        if not header or not self._access_token:
            return {}
        value = f"{scheme} {self._access_token}".strip()
        return {header: value}

    def create_account(
        self,
        requirement: dict[str, Any],
        *,
        username: str,
        password: str,
    ) -> dict[str, Any]:
        template = _parse_json_template(
            self.config.get("create_body"),
            key="create_body",
        )
        constraints = dict(requirement.get("constraints") or {})
        context = {
            "username": username,
            "password": password,
            "is_active": requirement.get("profile") != "dynamic_disabled",
            "profile": requirement.get("profile") or "dynamic_active",
            "account_full_name": TEST_ACCOUNT_FULL_NAME,
            **constraints,
        }
        payload = self._request(
            str(self.config.get("create_method") or "POST"),
            str(self.config.get("create_path") or DEFAULT_HTTP_CREATE_PATH),
            body=_render_template(template, context),
            headers=self._auth_headers(),
        )
        user_id = _extract_jsonpath(
            payload,
            str(self.config.get("user_id_jsonpath") or DEFAULT_HTTP_USER_ID_JSONPATH),
            field="user_id_jsonpath",
        )
        return {"user_id": user_id, "username": username}

    def cleanup_account(self, token: dict[str, Any]) -> None:
        path = _render_template(
            str(self.config.get("cleanup_path") or DEFAULT_HTTP_CLEANUP_PATH),
            token,
        )
        self._request(
            str(self.config.get("cleanup_method") or DEFAULT_HTTP_CLEANUP_METHOD),
            str(path),
            headers=self._auth_headers(),
            allow_not_found=True,
        )


def _validate_http_account_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not config.get("api_base_url"):
        errors.append(
            "HTTP 账号工厂缺少 test_accounts.api_base_url、web/browser.base_url "
            "或 api/host.url"
        )
    if not config.get("create_path"):
        errors.append("HTTP 账号工厂缺少 test_accounts.create_path")
    if config.get("auto_cleanup") and not config.get("cleanup_path"):
        errors.append("HTTP 账号工厂启用自动清理但缺少 test_accounts.cleanup_path")
    if config.get("login_path") and (
        not config.get("shared_username") or not config.get("shared_password")
    ):
        errors.append("HTTP 账号工厂登录接口需要 shared_username/shared_password")
    for key in ("login_body", "create_body"):
        try:
            _parse_json_template(config.get(key), key=key)
        except WebTestDataError as exc:
            errors.append(str(exc))
    return errors


def _workflow_script_exists(session: Session, project_id: int, name: str) -> bool:
    """检查项目或全局 workflow 脚本是否启用。"""
    if not name:
        return False
    return (
        session.query(ScriptStore.id)
        .filter(
            ScriptStore.name == name,
            ScriptStore.kind == SCRIPT_KIND_WORKFLOW,
            ScriptStore.enabled.is_(True),
            (ScriptStore.project_id == project_id) | (ScriptStore.project_id.is_(None)),
        )
        .first()
        is not None
    )


def _validate_script_account_config(
    session: Session,
    project_id: int,
    config: dict[str, Any],
) -> list[str]:
    """校验脚本账号工厂配置，不执行项目脚本。"""
    errors: list[str] = []
    prepare_script = str(config.get("prepare_script") or "").strip()
    cleanup_script = str(config.get("cleanup_script") or prepare_script).strip()
    if not prepare_script:
        errors.append("脚本账号工厂缺少 test_accounts.prepare_script")
    elif not _workflow_script_exists(session, project_id, prepare_script):
        errors.append(f"未找到启用的 workflow 脚本：{prepare_script}")
    if config.get("auto_cleanup") and cleanup_script and not _workflow_script_exists(
        session,
        project_id,
        cleanup_script,
    ):
        errors.append(f"未找到启用的清理 workflow 脚本：{cleanup_script}")
    try:
        _parse_json_template(config.get("script_config"), key="script_config")
    except WebTestDataError as exc:
        errors.append(str(exc))
    return errors


def validate_account_requirement(
    session: Session,
    project_id: int,
    requirement: dict[str, Any],
) -> list[str]:
    """提交/运行前校验账号需求是否可绑定。"""
    status = str(requirement.get("status") or "ready")
    if status != "ready":
        return [str(requirement.get("reason") or "测试数据前置条件不满足")]
    profile = str(requirement.get("profile") or "none")
    if profile in {"none", "form_empty", "synthetic_nonexistent"}:
        return []
    if profile == "isolated_lock_account" and os.getenv(
        "LOGIN_THROTTLE_ENABLED",
        "1",
    ).strip().lower() in {"0", "false", "no", "off"}:
        return ["当前环境已关闭登录限流，无法验证连续失败后的账号锁定"]
    config = load_test_account_config(session, project_id)
    if profile == "shared_admin":
        if not config["shared_username"] or not config["shared_password"]:
            return ["项目 Web 配置缺少 test_accounts.shared_username/shared_password"]
        return []
    if config["provider"] == TEST_ACCOUNT_PROVIDER_SCRIPT:
        return _validate_script_account_config(session, project_id, config)
    if config["provider"] != TEST_ACCOUNT_PROVIDER_HTTP:
        return [
            "该用例需要动态测试账号；请把 Web 配置 test_accounts.provider 设为 "
            "http 或 script，并配置目标系统账号工厂"
        ]
    return _validate_http_account_config(config)


def _script_runtime_config(config: dict[str, Any], project_id: int) -> dict[str, Any]:
    """构造脚本可见配置；平台内部连接和对象不会跨进程。"""
    custom = _parse_json_template(config.get("script_config"), key="script_config")
    return {
        "api_base_url": config.get("api_base_url") or "",
        "shared_username": config.get("shared_username") or "",
        "shared_password": config.get("shared_password") or "",
        **custom,
        "project_id": project_id,
    }


def _create_account_with_script(
    config: dict[str, Any],
    project_id: int,
    requirement: dict[str, Any],
    *,
    username: str,
    password: str,
) -> dict[str, Any]:
    """通过隔离 workflow 创建账号并归一化返回值。"""
    script_name = str(config.get("prepare_script") or "").strip()
    found, output = run_named_script(
        script_name,
        kind=SCRIPT_KIND_WORKFLOW,
        project_id=project_id,
        body={
            "action": "create",
            "requirement": requirement,
            "account": {
                "username": username,
                "password": password,
                "is_active": requirement.get("profile") != "dynamic_disabled",
                "full_name": TEST_ACCOUNT_FULL_NAME,
            },
        },
        config=_script_runtime_config(config, project_id),
        vars={},
        timeout=config.get("timeout_seconds") or 30,
    )
    if not found:
        raise WebTestDataError(f"未找到启用的 workflow 脚本：{script_name}")
    if not isinstance(output, dict):
        raise WebTestDataError("账号准备 workflow 必须返回 JSON 对象")
    if output.get("ok") is False:
        raise WebTestDataError(
            str(output.get("error") or output.get("message") or "账号准备脚本返回失败")
        )
    result = output.get("result")
    if result is None:
        result = {}
    if not isinstance(result, dict):
        raise WebTestDataError("账号准备 workflow 的 result 必须是 JSON 对象")
    variables = output.get("variables") or {}
    if not isinstance(variables, dict):
        raise WebTestDataError("账号准备 workflow 的 variables 必须是 JSON 对象")
    resolved_username = variables.get("username", result.get("username", username))
    resolved_password = variables.get("password", result.get("password", password))
    cleanup_payload = output.get("cleanup", result.get("cleanup_token", result))
    return {
        **result,
        "username": str(resolved_username),
        "password": str(resolved_password),
        "cleanup_payload": cleanup_payload,
    }


def _cleanup_account_with_script(
    config: dict[str, Any],
    project_id: int,
    token: dict[str, Any],
) -> None:
    """通过隔离 workflow 清理账号。"""
    script_name = str(
        token.get("script_name")
        or config.get("cleanup_script")
        or config.get("prepare_script")
        or ""
    ).strip()
    found, output = run_named_script(
        script_name,
        kind=SCRIPT_KIND_WORKFLOW,
        project_id=project_id,
        body={
            "action": "cleanup",
            "token": token.get("cleanup_payload"),
            "account": {
                "username": token.get("username"),
                "user_id": token.get("user_id"),
            },
        },
        config=_script_runtime_config(config, project_id),
        vars={},
        timeout=config.get("timeout_seconds") or 30,
    )
    if not found:
        raise WebTestDataError(f"未找到启用的清理 workflow 脚本：{script_name}")
    if isinstance(output, dict) and output.get("ok") is False:
        raise WebTestDataError(
            str(output.get("error") or output.get("message") or "账号清理脚本返回失败")
        )


def _temporary_username(constraints: dict[str, Any]) -> str:
    token = secrets.token_hex(6)
    if int(constraints.get("username_length") or 0) == 64:
        prefix = f"{TEST_ACCOUNT_USER_PREFIX}64_"
        return (prefix + token + "x" * 64)[:64]
    if constraints.get("username_allowed_special"):
        return f"AUTO.ui-test_{token}"
    return f"{TEST_ACCOUNT_USER_PREFIX}{token}"


def _temporary_password(constraints: dict[str, Any]) -> str:
    length = int(constraints.get("password_length") or 0)
    if length == 6:
        return "Aa1!x6"
    if length == 128:
        return "Aa1!" + "x" * 124
    return "AutoTest#123"


def _bind_value(variables: dict[str, Any], key: str, value: str) -> None:
    variables[key] = value


def prepare_web_test_data(
    session: Session,
    cases: list[dict[str, Any]],
    *,
    project_id: int,
) -> list[dict[str, Any]]:
    """运行前通过 HTTP/脚本账号工厂绑定变量，返回任务结束后的清理令牌。"""
    config = load_test_account_config(session, project_id)
    cleanup_tokens: list[dict[str, Any]] = []
    client: _HttpTestAccountClient | None = None
    try:
        for case in cases:
            metadata = dict(case.get("generation_metadata") or {})
            requirement = metadata.get("test_data_requirement")
            if not isinstance(requirement, dict):
                if str(case.get("source") or "") != "ai_m8_web":
                    continue
                requirement = infer_account_requirement(
                    case.get("name"),
                    case.get("description"),
                    case.get("variables") or {},
                )
            errors = validate_account_requirement(session, project_id, requirement)
            if errors:
                raise WebTestDataError(
                    f"用例“{case.get('name')}”测试数据未就绪：{'；'.join(errors)}"
                )

            variables = dict(case.get("variables") or {})
            username_key = str(requirement.get("username_variable") or "username")
            password_key = str(requirement.get("password_variable") or "password")
            profile = str(requirement.get("profile") or "none")
            mode = str(requirement.get("credential_mode") or "none")
            case_cleanup_tokens: list[dict[str, Any]] = []

            if profile == "form_empty":
                if mode in {"both_empty", "empty_username"}:
                    _bind_value(variables, username_key, "")
                else:
                    _bind_value(variables, username_key, f"AUTO_FORM_{secrets.token_hex(4)}")
                if mode in {"both_empty", "empty_password"}:
                    _bind_value(variables, password_key, "")
                else:
                    _bind_value(variables, password_key, "Validation#1")
            elif profile == "synthetic_nonexistent":
                _bind_value(variables, username_key, f"AUTO_MISSING_{secrets.token_hex(6)}")
                _bind_value(variables, password_key, "Wrong#1234")
            elif profile == "shared_admin":
                _bind_value(variables, username_key, config["shared_username"])
                _bind_value(variables, password_key, config["shared_password"])
            elif profile not in {"none", "unsupported"}:
                constraints = dict(requirement.get("constraints") or {})
                username = _temporary_username(constraints)
                correct_password = _temporary_password(constraints)
                if config["provider"] == TEST_ACCOUNT_PROVIDER_SCRIPT:
                    account = _create_account_with_script(
                        config,
                        project_id,
                        requirement,
                        username=username,
                        password=correct_password,
                    )
                    correct_password = account["password"]
                else:
                    if client is None:
                        client = _HttpTestAccountClient(config)
                    account = client.create_account(
                        requirement,
                        username=username,
                        password=correct_password,
                    )
                if config["auto_cleanup"]:
                    provider = str(config["provider"])
                    token: dict[str, Any] = {
                        "provider": provider,
                        "project_id": project_id,
                        "username": account["username"],
                    }
                    if provider == TEST_ACCOUNT_PROVIDER_SCRIPT:
                        token.update({
                            "token_id": secrets.token_hex(12),
                            "script_name": str(
                                config.get("cleanup_script")
                                or config.get("prepare_script")
                                or ""
                            ),
                            "cleanup_payload": account.get("cleanup_payload"),
                            "user_id": account.get("user_id"),
                        })
                    else:
                        token["user_id"] = account["user_id"]
                    cleanup_tokens.append(token)
                    case_cleanup_tokens.append(token)
                _bind_value(variables, username_key, account["username"])
                _bind_value(
                    variables,
                    password_key,
                    "Wrong#1234" if mode == "wrong" else correct_password,
                )
            case["variables"] = variables
            case["_test_data_cleanup_tokens"] = case_cleanup_tokens
        return cleanup_tokens
    except Exception:
        # 外部创建是独立事务；准备中途失败时必须立即回收已创建账号。
        for token in reversed(cleanup_tokens):
            try:
                if token.get("provider") == TEST_ACCOUNT_PROVIDER_SCRIPT:
                    _cleanup_account_with_script(config, project_id, token)
                elif client is not None:
                    client.cleanup_account(token)
            except Exception:
                pass
        raise
    finally:
        if client is not None:
            client.close()


def cleanup_web_test_accounts(
    session: Session,
    cleanup_tokens: list[dict[str, Any]],
) -> int:
    """通过项目配置的 HTTP 接口或 workflow 回收临时账号。"""
    unique: dict[tuple[int, str, str], dict[str, Any]] = {}
    for token in cleanup_tokens:
        if not isinstance(token, dict):
            continue
        try:
            project_id = int(token.get("project_id") or 0)
        except (TypeError, ValueError):
            continue
        provider = str(token.get("provider") or TEST_ACCOUNT_PROVIDER_HTTP).strip()
        identity = str(
            token.get("token_id")
            or token.get("user_id")
            or token.get("username")
            or ""
        ).strip()
        if project_id > 0 and identity:
            unique[(project_id, provider, identity)] = dict(token)
    if not unique:
        return 0

    clients: dict[int, _HttpTestAccountClient] = {}
    cleaned = 0
    failures: list[str] = []
    try:
        for (project_id, provider, identity), token in unique.items():
            if provider == TEST_ACCOUNT_PROVIDER_SCRIPT:
                try:
                    _cleanup_account_with_script(
                        load_test_account_config(session, project_id),
                        project_id,
                        token,
                    )
                    cleaned += 1
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"script_token={identity}: {exc}")
                continue
            client = clients.get(project_id)
            if client is None:
                client = _HttpTestAccountClient(
                    load_test_account_config(session, project_id)
                )
                clients[project_id] = client
            try:
                client.cleanup_account(token)
                cleaned += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"user_id={identity}: {exc}")
    finally:
        for client in clients.values():
            client.close()
    if failures:
        raise WebTestDataError("；".join(failures))
    return cleaned
