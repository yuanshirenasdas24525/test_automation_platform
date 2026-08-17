# 通用化测试账号/数据准备 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Web UI 用例运行前的账号/数据准备从"假设 SUT=平台自己"改造为"静态账号池为主 + 唯一脚本动态钩子"，平台核心对 SUT 零出站 HTTP、零平台特定默认，并删除直连库重置密码的自测 hack。

**Architecture:** 把单文件 `server/services/web_test_data_service.py` 拆成小包 `server/services/test_accounts/`（secrets/requirements/sources/resolver/binding）。删除 `_HttpTestAccountClient` 及全部 `DEFAULT_HTTP_*`；`resolve_account` 按"本地生成 / 静态池按 state 匹配 / 脚本兜底"确定性解析；保留既有 workflow 脚本契约作为唯一动态钩子。

**Tech Stack:** Python 3.13、SQLAlchemy 2.0、FastAPI、pytest、Alembic、`utils.crypto`（密码加密）、`utils.script_runtime.run_named_script`（脚本钩子）。

**依据 spec:** `docs/superpowers/specs/2026-08-18-universalize-test-account-provisioning-design.md`

---

## 关键映射：requirement.profile → 解析方式（贯穿全计划）

| profile | lifecycle | 解析方式 | 静态池 state |
|---|---|---|---|
| `none` | none | 不绑定凭据 | — |
| `form_empty` | none | 本地按 credential_mode 生成空/占位值 | — |
| `synthetic_nonexistent` | none | 本地生成"每轮唯一且不存在"用户名 + 错误密码 | — |
| `shared_admin` | shared | 真实账号 | `admin` |
| `dynamic_active` | dynamic | 真实账号 | `normal` |
| `dynamic_disabled` | dynamic | 真实账号 | `disabled` |
| `dynamic_boundary` | dynamic | 真实账号（constraints 仅传给脚本，静态池账号按声明用） | `boundary` |
| `isolated_lock_account`（status=ready） | dynamic | 真实账号（破坏性，建议脚本） | `locked` |
| status ≠ ready（unsupported/contract_mismatch/isolated 不满足） | — | 校验直接报错，不解析 | — |

"要真实账号"时的**确定性解析顺序**：① 静态池有 `enabled` 且 `state` 命中 → 用它；② 否则配了 `dynamic_script` → 调脚本；③ 否则 `WebTestDataError`。匹配时 `normal` 允许回落到 `admin`。

## 文件结构

- 新建包 `server/services/test_accounts/`
  - `__init__.py` — 重导出公开符号
  - `secrets.py` — 密码 encode/decode/mask + `is_test_account_secret`
  - `requirements.py` — `infer_account_requirement`（纯推断）+ marker 常量
  - `sources.py` — `load_account_sources`（静态池 + dynamic_script）
  - `resolver.py` — `resolve_account` + `validate_account_requirement` + profile→state
  - `binding.py` — `prepare_web_test_data` + `cleanup_web_test_accounts`
- 删除 `server/services/web_test_data_service.py`
- 改 `server/api/config_schemas.py`、`config/pytest_config.py`、及 import 触点
- 新建数据迁移 `database/migrations/data_migrations/migrate_test_accounts_to_pool.py`

---

## Task 1: 抽出 secrets.py（密码加解密/掩码）

**Files:**
- Create: `server/services/test_accounts/__init__.py`
- Create: `server/services/test_accounts/secrets.py`
- Test: `tests/services/test_accounts/test_secrets.py`

- [ ] **Step 1: 写失败测试**

`tests/services/test_accounts/test_secrets.py`:
```python
from server.services.test_accounts.secrets import (
    TEST_ACCOUNT_SECRET_MASK,
    decode_test_account_secret,
    encode_test_account_secret,
    is_test_account_secret,
    mask_test_account_config,
    prepare_test_account_config_value,
)


def test_encode_decode_roundtrip():
    enc = encode_test_account_secret("s3cret")
    assert enc.startswith("enc:v1:")
    assert decode_test_account_secret(enc) == "s3cret"


def test_is_secret_targets_account_password_keys():
    assert is_test_account_secret("test_accounts", "account_password") is True
    assert is_test_account_secret("test_accounts", "dynamic_script") is False
    assert is_test_account_secret("browser", "base_url") is False


def test_mask_hides_password():
    enc = encode_test_account_secret("p")
    assert mask_test_account_config("test_accounts", "account_password", enc) == TEST_ACCOUNT_SECRET_MASK
    assert mask_test_account_config("test_accounts", "dynamic_script", "x") == "x"


def test_prepare_keeps_existing_on_mask_or_empty():
    assert prepare_test_account_config_value("test_accounts", "account_password", "", existing="old") == "old"
    assert prepare_test_account_config_value("test_accounts", "account_password", TEST_ACCOUNT_SECRET_MASK, existing="old") == "old"
    new = prepare_test_account_config_value("test_accounts", "account_password", "new", existing="old")
    assert decode_test_account_secret(new) == "new"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/services/test_accounts/test_secrets.py -q`
Expected: FAIL（`ModuleNotFoundError: server.services.test_accounts`）

- [ ] **Step 3: 建包与 secrets.py**

`server/services/test_accounts/__init__.py`:
```python
"""Web UI 测试账号/数据准备（通用化）。"""
```

`server/services/test_accounts/secrets.py`（从旧文件搬 crypto helper，密钥键名改为账号池内 `account_password`）:
```python
from __future__ import annotations

from typing import Any

from utils.crypto import decrypt_secret, encrypt_secret

TEST_ACCOUNT_CONFIG_GROUP = "test_accounts"
TEST_ACCOUNT_SECRET_PREFIX = "enc:v1:"
TEST_ACCOUNT_SECRET_MASK = "••••••••"
# 池内每个账号的密码键；配置写入时按此键加密、读取时掩码。
_SECRET_KEYS = {"account_password"}


def is_test_account_secret(group: str | None, key: str | None) -> bool:
    return (
        str(group or "").strip().lower() == TEST_ACCOUNT_CONFIG_GROUP
        and str(key or "").strip().lower() in _SECRET_KEYS
    )


def encode_test_account_secret(value: str) -> str:
    text = str(value or "")
    if text.startswith(TEST_ACCOUNT_SECRET_PREFIX):
        return text
    return TEST_ACCOUNT_SECRET_PREFIX + encrypt_secret(text)


def decode_test_account_secret(value: str | None) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith(TEST_ACCOUNT_SECRET_PREFIX):
        return decrypt_secret(text[len(TEST_ACCOUNT_SECRET_PREFIX):])
    return text


def mask_test_account_config(group: str | None, key: str | None, value: Any) -> Any:
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
    if not is_test_account_secret(group, key):
        return value
    text = str(value or "")
    if text in {"", TEST_ACCOUNT_SECRET_MASK}:
        return existing
    return encode_test_account_secret(text)
```

创建空文件 `tests/services/test_accounts/__init__.py`（若 tests 目录使用包结构）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/services/test_accounts/test_secrets.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/services/test_accounts/__init__.py server/services/test_accounts/secrets.py tests/services/test_accounts/
git commit -m "feat(test_accounts): 抽出 secrets 模块（密码加解密/掩码）"
```

---

## Task 2: 抽出 requirements.py（用例需求推断）

**Files:**
- Create: `server/services/test_accounts/requirements.py`
- Test: `tests/services/test_accounts/test_requirements.py`

- [ ] **Step 1: 写失败测试**

`tests/services/test_accounts/test_requirements.py`:
```python
from server.services.test_accounts.requirements import infer_account_requirement


def test_page_load_needs_no_account():
    r = infer_account_requirement("首页页面加载展示", None, {"username": ""})
    assert r["profile"] == "none"


def test_both_empty_form():
    r = infer_account_requirement("用户名和密码均为空登录", None, {"username": "", "password": ""})
    assert r["profile"] == "form_empty"
    assert r["credential_mode"] == "both_empty"


def test_shared_admin():
    r = infer_account_requirement("admin 使用默认密码成功登录", None, {"username": "admin", "password": "x"})
    assert r["profile"] == "shared_admin"
    assert r["lifecycle"] == "shared"


def test_disabled_account_is_dynamic():
    r = infer_account_requirement("已停用账号登录被拒", None, {"username": "u", "password": "p"})
    assert r["profile"] == "dynamic_disabled"
    assert r["lifecycle"] == "dynamic"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/services/test_accounts/test_requirements.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 搬 requirements.py**

把旧文件 `web_test_data_service.py` 中以下内容**原样剪切**到 `server/services/test_accounts/requirements.py`：`_USERNAME_MARKERS`、`_PASSWORD_MARKERS` 常量、`_variable_key`、`infer_account_requirement`。文件头加：
```python
from __future__ import annotations

import re
from typing import Any
```
（`infer_account_requirement` 主体逐行照搬旧代码，不改逻辑。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/services/test_accounts/test_requirements.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/services/test_accounts/requirements.py tests/services/test_accounts/test_requirements.py
git commit -m "feat(test_accounts): 抽出 requirements 模块（需求推断）"
```

---

## Task 3: sources.py（读静态池 + dynamic_script，无平台默认）

**Files:**
- Create: `server/services/test_accounts/sources.py`
- Test: `tests/services/test_accounts/test_sources.py`

数据结构：
```python
# AccountEntry: {"label": str, "username": str, "password": str(明文，已解密),
#                "state": "normal|admin|disabled|locked|boundary", "enabled": bool}
# AccountSources: {"accounts": list[AccountEntry], "dynamic_script": str}
```

- [ ] **Step 1: 写失败测试**

`tests/services/test_accounts/test_sources.py`:
```python
from server.services.test_accounts.secrets import encode_test_account_secret
from server.services.test_accounts.sources import load_account_sources


class _Row:
    def __init__(self, key, value):
        self.config_key = key
        self.config_value = value


class _Query:
    def __init__(self, rows):
        self._rows = rows
    def filter(self, *a, **k):
        return self
    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows
    def query(self, *a, **k):
        return _Query(self._rows)


def test_load_pool_decrypts_password_and_defaults_enabled():
    rows = [
        _Row("accounts", [
            {"label": "普通", "username": "u1", "password": encode_test_account_secret("p1"), "state": "normal"},
        ]),
        _Row("dynamic_script", "provision_fresh"),
    ]
    src = load_account_sources(_Session(rows), project_id=1)
    assert src["dynamic_script"] == "provision_fresh"
    acc = src["accounts"][0]
    assert acc["username"] == "u1"
    assert acc["password"] == "p1"           # 已解密
    assert acc["enabled"] is True            # 缺省启用
    assert acc["state"] == "normal"


def test_missing_config_yields_empty_sources():
    src = load_account_sources(_Session([]), project_id=1)
    assert src["accounts"] == []
    assert src["dynamic_script"] == ""


def test_malformed_accounts_value_is_ignored():
    rows = [_Row("accounts", "not-a-list")]
    src = load_account_sources(_Session(rows), project_id=1)
    assert src["accounts"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/services/test_accounts/test_sources.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 sources.py**

```python
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models import ConfigStore
from server.services.test_accounts.secrets import (
    TEST_ACCOUNT_CONFIG_GROUP,
    decode_test_account_secret,
)

_VALID_STATES = {"normal", "admin", "disabled", "locked", "boundary"}


def _coerce_accounts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        state = str(item.get("state") or "normal").strip().lower()
        if state not in _VALID_STATES:
            state = "normal"
        out.append({
            "label": str(item.get("label") or username),
            "username": username,
            "password": decode_test_account_secret(item.get("password")),
            "state": state,
            "enabled": item.get("enabled", True) is not False,
        })
    return out


def load_account_sources(session: Session, project_id: int) -> dict[str, Any]:
    """读项目账号来源：静态池 + 可选 dynamic_script。无 HTTP 字段、无平台默认。"""
    rows = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.project_id == project_id,
            ConfigStore.category == "web",
            ConfigStore.config_group == TEST_ACCOUNT_CONFIG_GROUP,
        )
        .all()
    )
    config = {str(r.config_key): r.config_value for r in rows}
    return {
        "accounts": _coerce_accounts(config.get("accounts")),
        "dynamic_script": str(config.get("dynamic_script") or "").strip(),
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/services/test_accounts/test_sources.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/services/test_accounts/sources.py tests/services/test_accounts/test_sources.py
git commit -m "feat(test_accounts): sources 读静态池+dynamic_script（无平台默认）"
```

---

## Task 4: resolver.py（解析账号 + 校验需求）

**Files:**
- Create: `server/services/test_accounts/resolver.py`
- Test: `tests/services/test_accounts/test_resolver.py`

- [ ] **Step 1: 写失败测试**

`tests/services/test_accounts/test_resolver.py`:
```python
import pytest

from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.resolver import resolve_account


def _req(profile, credential_mode="correct", **extra):
    return {
        "status": "ready", "profile": profile, "credential_mode": credential_mode,
        "username_variable": "username", "password_variable": "password", **extra,
    }


def _sources(accounts=None, dynamic_script=""):
    return {"accounts": accounts or [], "dynamic_script": dynamic_script}


def test_none_profile_binds_nothing():
    r = resolve_account(_req("none", "none"), _sources(), session=None, project_id=1)
    assert r.bindings == {}
    assert r.cleanup_token is None


def test_form_empty_both_empty():
    r = resolve_account(_req("form_empty", "both_empty"), _sources(), session=None, project_id=1)
    assert r.bindings == {"username": "", "password": ""}


def test_synthetic_nonexistent_is_unique_and_wrong():
    r = resolve_account(_req("synthetic_nonexistent", "wrong"), _sources(), session=None, project_id=1)
    assert r.bindings["username"].startswith("AUTO_MISSING_")
    assert r.bindings["password"]


def test_static_pool_match_by_state():
    accts = [{"label": "普通", "username": "u1", "password": "p1", "state": "normal", "enabled": True}]
    r = resolve_account(_req("dynamic_active"), _sources(accts), session=None, project_id=1)
    assert r.bindings == {"username": "u1", "password": "p1"}
    assert r.cleanup_token is None


def test_normal_falls_back_to_admin():
    accts = [{"label": "管理员", "username": "admin", "password": "pa", "state": "admin", "enabled": True}]
    r = resolve_account(_req("dynamic_active"), _sources(accts), session=None, project_id=1)
    assert r.bindings["username"] == "admin"


def test_disabled_pool_entry_is_skipped():
    accts = [{"label": "普通", "username": "u1", "password": "p1", "state": "normal", "enabled": False}]
    with pytest.raises(WebTestDataError):
        resolve_account(_req("dynamic_active"), _sources(accts), session=None, project_id=1)


def test_no_match_no_script_raises_actionable():
    with pytest.raises(WebTestDataError) as e:
        resolve_account(_req("dynamic_disabled"), _sources(), session=None, project_id=7)
    assert "disabled" in str(e.value) or "停用" in str(e.value)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/services/test_accounts/test_resolver.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 errors.py + resolver.py**

`server/services/test_accounts/errors.py`:
```python
class WebTestDataError(ValueError):
    """测试数据无法安全准备。"""
```

`server/services/test_accounts/resolver.py`:
```python
from __future__ import annotations

import os
import secrets as _secrets
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from database.models.script_store import SCRIPT_KIND_WORKFLOW
from server.services.test_accounts.errors import WebTestDataError
from utils.script_runtime import run_named_script

TEST_ACCOUNT_FULL_NAME = "Web UI 自动化临时账号"

# profile → 静态池 state
_PROFILE_STATE = {
    "shared_admin": "admin",
    "dynamic_active": "normal",
    "dynamic_disabled": "disabled",
    "dynamic_boundary": "boundary",
    "isolated_lock_account": "locked",
}
_STATE_LABEL = {
    "admin": "管理员", "normal": "普通", "disabled": "停用",
    "locked": "锁定", "boundary": "边界",
}


@dataclass
class ResolvedAccount:
    bindings: dict[str, str] = field(default_factory=dict)
    cleanup_token: dict[str, Any] | None = None


def validate_account_requirement(
    session: Session, project_id: int, requirement: dict[str, Any]
) -> list[str]:
    """运行/提交前校验需求可否绑定；返回错误文案列表（空=可绑定）。"""
    status = str(requirement.get("status") or "ready")
    if status != "ready":
        return [str(requirement.get("reason") or "测试数据前置条件不满足")]
    profile = str(requirement.get("profile") or "none")
    if profile in {"none", "form_empty", "synthetic_nonexistent"}:
        return []
    if profile == "isolated_lock_account" and os.getenv(
        "LOGIN_THROTTLE_ENABLED", "1"
    ).strip().lower() in {"0", "false", "no", "off"}:
        return ["当前环境已关闭登录限流，无法验证连续失败后的账号锁定"]
    # 需要真实账号：静态池或脚本二选一可满足即可
    from server.services.test_accounts.sources import load_account_sources

    sources = load_account_sources(session, project_id)
    if _pick_pool_account(sources["accounts"], profile) is not None:
        return []
    if sources["dynamic_script"]:
        return []
    state = _PROFILE_STATE.get(profile, "normal")
    return [
        f"项目未声明满足『{_STATE_LABEL.get(state, state)}』的测试账号，"
        f"请在账号池补充或配置 dynamic_script"
    ]


def _pick_pool_account(accounts: list[dict[str, Any]], profile: str) -> dict[str, Any] | None:
    state = _PROFILE_STATE.get(profile, "normal")
    enabled = [a for a in accounts if a.get("enabled", True)]
    for a in enabled:
        if a.get("state") == state:
            return a
    if state == "normal":  # normal 可回落 admin
        for a in enabled:
            if a.get("state") == "admin":
                return a
    return None


def resolve_account(
    requirement: dict[str, Any],
    sources: dict[str, Any],
    *,
    session: Session | None,
    project_id: int,
) -> ResolvedAccount:
    profile = str(requirement.get("profile") or "none")
    mode = str(requirement.get("credential_mode") or "none")
    ukey = str(requirement.get("username_variable") or "username")
    pkey = str(requirement.get("password_variable") or "password")

    if profile == "none":
        return ResolvedAccount()
    if profile == "form_empty":
        username = "" if mode in {"both_empty", "empty_username"} else f"AUTO_FORM_{_secrets.token_hex(4)}"
        password = "" if mode in {"both_empty", "empty_password"} else "Validation#1"
        return ResolvedAccount(bindings={ukey: username, pkey: password})
    if profile == "synthetic_nonexistent":
        return ResolvedAccount(bindings={
            ukey: f"AUTO_MISSING_{_secrets.token_hex(6)}", pkey: "Wrong#1234",
        })

    # 真实账号：静态池优先 → 脚本兜底 → 报错
    account = _pick_pool_account(sources["accounts"], profile)
    if account is not None:
        return ResolvedAccount(bindings={ukey: account["username"], pkey: account["password"]})

    script_name = str(sources.get("dynamic_script") or "").strip()
    if script_name:
        return _resolve_via_script(script_name, requirement, project_id, ukey, pkey)

    state = _PROFILE_STATE.get(profile, "normal")
    raise WebTestDataError(
        f"项目未声明满足『{_STATE_LABEL.get(state, state)}』的测试账号，"
        f"请在账号池补充或配置 dynamic_script"
    )


def _resolve_via_script(
    script_name: str, requirement: dict[str, Any], project_id: int, ukey: str, pkey: str
) -> ResolvedAccount:
    username = f"AUTO_UI_{_secrets.token_hex(4)}"
    password = f"Auto#{_secrets.token_hex(4)}"
    found, output = run_named_script(
        script_name,
        kind=SCRIPT_KIND_WORKFLOW,
        project_id=project_id,
        body={
            "action": "create",
            "requirement": requirement,
            "account": {
                "username": username, "password": password,
                "is_active": requirement.get("profile") != "dynamic_disabled",
                "full_name": TEST_ACCOUNT_FULL_NAME,
            },
        },
        config={"project_id": project_id},
        vars={},
        timeout=30,
    )
    if not found:
        raise WebTestDataError(f"未找到启用的 workflow 脚本：{script_name}")
    if not isinstance(output, dict) or output.get("ok") is False:
        reason = output.get("error") or output.get("message") if isinstance(output, dict) else None
        raise WebTestDataError(str(reason or "账号准备脚本返回失败"))
    result = output.get("result") if isinstance(output.get("result"), dict) else {}
    variables = output.get("variables") if isinstance(output.get("variables"), dict) else {}
    resolved_username = str(variables.get("username", result.get("username", username)))
    resolved_password = str(variables.get("password", result.get("password", password)))
    cleanup_payload = output.get("cleanup", result.get("cleanup_token", result))
    return ResolvedAccount(
        bindings={ukey: resolved_username, pkey: resolved_password},
        cleanup_token={"script_name": script_name, "project_id": project_id, "payload": cleanup_payload},
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/services/test_accounts/test_resolver.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/services/test_accounts/errors.py server/services/test_accounts/resolver.py tests/services/test_accounts/test_resolver.py
git commit -m "feat(test_accounts): resolver 静态池优先+脚本兜底+需求校验"
```

---

## Task 5: binding.py（编排入口 + 清理）并接线 runs/run_test_task

**Files:**
- Create: `server/services/test_accounts/binding.py`
- Modify: `server/services/test_accounts/__init__.py`
- Modify: `server/api/runs.py`（import 改到新包）
- Modify: `tasks/run_test_task.py:157`（import 改到新包）
- Test: `tests/services/test_accounts/test_binding.py`

- [ ] **Step 1: 写失败测试**

`tests/services/test_accounts/test_binding.py`:
```python
from server.services.test_accounts import binding


def test_prepare_binds_static_pool_account(monkeypatch):
    monkeypatch.setattr(binding, "load_account_sources", lambda *_: {
        "accounts": [{"label": "普通", "username": "u1", "password": "p1", "state": "normal", "enabled": True}],
        "dynamic_script": "",
    })
    cases = [{
        "name": "登录成功", "variables": {"username": "", "password": ""},
        "generation_metadata": {"test_data_requirement": {
            "status": "ready", "profile": "dynamic_active", "credential_mode": "correct",
            "username_variable": "username", "password_variable": "password",
        }},
    }]
    tokens = binding.prepare_web_test_data(None, cases, project_id=1)
    assert cases[0]["variables"]["username"] == "u1"
    assert cases[0]["variables"]["password"] == "p1"
    assert tokens == []


def test_prepare_collects_cleanup_token_from_script(monkeypatch):
    monkeypatch.setattr(binding, "load_account_sources", lambda *_: {
        "accounts": [], "dynamic_script": "provision",
    })
    monkeypatch.setattr(binding, "run_named_script", lambda *a, **k: (True, {
        "ok": True, "result": {"username": "fresh", "password": "pw"},
        "cleanup": {"user_id": 9},
    }))
    cases = [{
        "name": "停用账号", "variables": {"username": "", "password": ""},
        "generation_metadata": {"test_data_requirement": {
            "status": "ready", "profile": "dynamic_disabled", "credential_mode": "correct",
            "username_variable": "username", "password_variable": "password",
        }},
    }]
    tokens = binding.prepare_web_test_data(None, cases, project_id=1)
    assert cases[0]["variables"]["username"] == "fresh"
    assert len(tokens) == 1
    assert tokens[0]["script_name"] == "provision"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/services/test_accounts/test_binding.py -q`
Expected: FAIL（`AttributeError`/`ImportError`）

- [ ] **Step 3: 实现 binding.py**

```python
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models.script_store import SCRIPT_KIND_WORKFLOW
from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.requirements import infer_account_requirement
from server.services.test_accounts.resolver import (
    resolve_account,
    validate_account_requirement,
)
from server.services.test_accounts.sources import load_account_sources
from utils.script_runtime import run_named_script


def prepare_web_test_data(
    session: Session, cases: list[dict[str, Any]], *, project_id: int
) -> list[dict[str, Any]]:
    """运行前为每条 web 用例绑定账号变量，返回需清理的脚本令牌。"""
    sources = load_account_sources(session, project_id)
    cleanup_tokens: list[dict[str, Any]] = []
    for case in cases:
        metadata = dict(case.get("generation_metadata") or {})
        requirement = metadata.get("test_data_requirement")
        if not isinstance(requirement, dict):
            if str(case.get("source") or "") != "ai_m8_web":
                continue
            requirement = infer_account_requirement(
                case.get("name"), case.get("description"), case.get("variables") or {}
            )
        errors = validate_account_requirement(session, project_id, requirement)
        if errors:
            raise WebTestDataError(
                f"用例“{case.get('name')}”测试数据未就绪：{'；'.join(errors)}"
            )
        resolved = resolve_account(
            requirement, sources, session=session, project_id=project_id
        )
        if resolved.bindings:
            variables = dict(case.get("variables") or {})
            variables.update(resolved.bindings)
            case["variables"] = variables
        if resolved.cleanup_token is not None:
            cleanup_tokens.append(resolved.cleanup_token)
    return cleanup_tokens


def cleanup_web_test_accounts(
    tokens: list[dict[str, Any]], *, project_id: int | None = None
) -> None:
    """任务收尾：对脚本造的号调 workflow 清理。静态池账号无需清理。"""
    for token in tokens or []:
        script_name = str(token.get("script_name") or "").strip()
        if not script_name:
            continue
        pid = token.get("project_id") if project_id is None else project_id
        try:
            run_named_script(
                script_name,
                kind=SCRIPT_KIND_WORKFLOW,
                project_id=pid,
                body={"action": "cleanup", "cleanup": token.get("payload")},
                config={"project_id": pid},
                vars={},
                timeout=30,
            )
        except Exception:  # noqa: BLE001 —— 清理失败不阻塞收尾
            pass
```

- [ ] **Step 4: __init__ 重导出 + 接线 import**

`server/services/test_accounts/__init__.py`:
```python
"""Web UI 测试账号/数据准备（通用化）。"""
from server.services.test_accounts.binding import (
    cleanup_web_test_accounts,
    prepare_web_test_data,
)
from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.requirements import infer_account_requirement
from server.services.test_accounts.resolver import validate_account_requirement
from server.services.test_accounts.secrets import (
    TEST_ACCOUNT_CONFIG_GROUP,
    decode_test_account_secret,
    encode_test_account_secret,
    is_test_account_secret,
    mask_test_account_config,
    prepare_test_account_config_value,
)

__all__ = [
    "cleanup_web_test_accounts", "prepare_web_test_data", "WebTestDataError",
    "infer_account_requirement", "validate_account_requirement",
    "TEST_ACCOUNT_CONFIG_GROUP", "decode_test_account_secret",
    "encode_test_account_secret", "is_test_account_secret",
    "mask_test_account_config", "prepare_test_account_config_value",
]
```

在 `server/api/runs.py` 里把
`from server.services.web_test_data_service import (WebTestDataError, prepare_web_test_data)`
改为
`from server.services.test_accounts import WebTestDataError, prepare_web_test_data`。

在 `tasks/run_test_task.py:157` 把
`from server.services.web_test_data_service import cleanup_web_test_accounts`
改为
`from server.services.test_accounts import cleanup_web_test_accounts`。

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/services/test_accounts/ -q`
Expected: PASS（全部）

- [ ] **Step 6: 提交**

```bash
git add server/services/test_accounts/ tests/services/test_accounts/test_binding.py server/api/runs.py tasks/run_test_task.py
git commit -m "feat(test_accounts): binding 编排+清理，接线 runs/run_test_task"
```

---

## Task 6: 删除旧文件与 HTTP 工厂，改剩余 import

**Files:**
- Delete: `server/services/web_test_data_service.py`
- Delete: `tests/services/test_web_test_data_service.py`
- Modify: `server/api/config.py:21-22`
- Modify: `server/api/users.py:22`
- Modify: `server/services/web_ui_case_generation_service.py:31`
- Modify: 历史迁移中的 import（见 Step 2）

- [ ] **Step 1: 改所有剩余 import 到新包**

- `server/api/config.py`：`from server.services.web_test_data_service import (mask_test_account_config, ...)` → `from server.services.test_accounts import (mask_test_account_config, ...)`（若还引 `prepare_test_account_config_value`/`is_test_account_secret` 一并改）。
- `server/api/users.py:22`：同理，`from server.services.test_accounts import ...`（保留原符号名，均已在 `__init__` 重导出）。
- `server/services/web_ui_case_generation_service.py:31`：`from server.services.test_accounts import infer_account_requirement`（及其它引用的符号）。
- 历史迁移 `database/migrations/data_migrations/repair_web_ui_login_success_assertions.py:16`、`backfill_web_ui_account_requirements.py:12`：`from server.services.test_accounts import infer_account_requirement, TEST_ACCOUNT_CONFIG_GROUP`。

- [ ] **Step 2: 删旧文件**

```bash
git rm server/services/web_test_data_service.py tests/services/test_web_test_data_service.py
```

- [ ] **Step 3: 全量 import 自检**

Run: `python -c "import server.main"` 与 `python -m compileall server tasks database -q`
Expected: 无 ImportError、无语法错误
（若报某处仍 `from server.services.web_test_data_service import ...`，改到新包后重跑。）

- [ ] **Step 4: 跑账号相关测试**

Run: `python -m pytest tests/services/test_accounts/ -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor(test_accounts): 删除旧 web_test_data_service 与 HTTP 工厂，改全部 import"
```

---

## Task 7: 精简 config_schemas 的账号工厂 schema

**Files:**
- Modify: `server/api/config_schemas.py`（`WEB_TEST_ACCOUNT_CONFIG_SCHEMA`，约 629–840 行）

- [ ] **Step 1: 替换 schema 定义**

把 `WEB_TEST_ACCOUNT_CONFIG_SCHEMA` 整体替换为下面两项（沿用该文件现有条目字段约定：`config_group/key/label/type/description/default/example/secret` 等；`accounts` 用 `json` 类型、`dynamic_script` 用 `string`）：
```python
# Web 测试账号：静态池 + 可选动态脚本。池内密码由配置 API 加密保存，列表接口只返回掩码。
WEB_TEST_ACCOUNT_CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "config_group": "test_accounts",
        "key": "accounts",
        "label": "静态账号池",
        "type": "json",
        "description": (
            "已在被测系统预备好的测试账号数组；每项 "
            '{"label","username","password","state","enabled"}，'
            "state ∈ normal/admin/disabled/locked/boundary。password 加密保存。"
        ),
        "default": [],
        "example": [
            {"label": "普通用户", "username": "qa_normal", "password": "", "state": "normal", "enabled": True},
            {"label": "管理员", "username": "qa_admin", "password": "", "state": "admin", "enabled": True},
        ],
    },
    {
        "config_group": "test_accounts",
        "key": "dynamic_script",
        "label": "动态账号脚本",
        "type": "string",
        "description": (
            "可选。脚本库中 workflow 脚本名；当静态池无匹配账号时，"
            "由它按 action=create/cleanup 造号/清号。留空=只用静态池。"
        ),
        "default": "",
        "example": "provision_fresh_account",
    },
]
```
删除原 schema 里 `api_base_url / login_* / token_jsonpath / auth_* / create_* / user_id_jsonpath / cleanup_* / timeout_seconds / provider / shared_username / shared_password` 全部条目。

> 注意：`accounts` 是账号数组，池内 `password` 的加密/掩码由 `server/api/config.py` 的写入/读取路径按 `is_test_account_secret("test_accounts","account_password")` 处理——若现有 config 写入/掩码逻辑是按顶层 `config_key` 判断，需在 config.py 里对 `accounts` 数组内每个 `password` 元素单独加密/掩码（在 Step 2 校验）。

- [ ] **Step 2: 校验 config 读写对池内密码的加密/掩码**

Run: `python -m pytest tests/ -q -k "config and account"` 或手动核对 `server/api/config.py` 写入/列出路径。
Expected: 写入 `accounts` 时每个 `password` 变成 `enc:v1:…`；列出时变 `••••••••`。若未覆盖，补一个针对 `accounts` 数组逐元素加密/掩码的小函数并接入 config.py（保持在本 Task 内）。

- [ ] **Step 3: 提交**

```bash
git add server/api/config_schemas.py server/api/config.py
git commit -m "feat(config): 账号工厂 schema 精简为静态池+dynamic_script"
```

---

## Task 8: 删除 pytest 直连库重置密码 hack

**Files:**
- Modify: `config/pytest_config.py`（删除 `_calibrate_shared_accounts` 定义与调用）

- [ ] **Step 1: 删除定义与调用**

删掉 `config/pytest_config.py` 第 119 行起的整个 `def _calibrate_shared_accounts(session)`，以及第 207 行 `_calibrate_shared_accounts(session)` 调用行。

- [ ] **Step 2: 语法与导入自检**

Run: `python -m compileall config/pytest_config.py -q`
Expected: 无错误

- [ ] **Step 3: 提交**

```bash
git add config/pytest_config.py
git commit -m "refactor: 删除直连库重置共享账号密码的自测 hack（改由项目脚本表达）"
```

---

## Task 9: 旧配置数据迁移

**Files:**
- Create: `database/migrations/data_migrations/migrate_test_accounts_to_pool.py`
- Test: `tests/migrations/test_migrate_test_accounts_to_pool.py`

- [ ] **Step 1: 写失败测试（用内存假 session 验证映射函数）**

`tests/migrations/test_migrate_test_accounts_to_pool.py`:
```python
from database.migrations.data_migrations.migrate_test_accounts_to_pool import (
    build_pool_from_legacy,
)


def test_shared_admin_maps_to_admin_pool_entry():
    legacy = {"shared_username": "demo_admin", "shared_password": "enc:v1:x"}
    pool, dynamic = build_pool_from_legacy(legacy)
    assert dynamic == ""
    assert pool == [{
        "label": "共享账号", "username": "demo_admin",
        "password": "enc:v1:x", "state": "admin", "enabled": True,
    }]


def test_script_provider_maps_to_dynamic_script():
    legacy = {"provider": "script", "prepare_script": "provision"}
    pool, dynamic = build_pool_from_legacy(legacy)
    assert dynamic == "provision"


def test_empty_legacy_yields_empty():
    assert build_pool_from_legacy({}) == ([], "")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/migrations/test_migrate_test_accounts_to_pool.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现迁移（纯函数 + apply）**

`database/migrations/data_migrations/migrate_test_accounts_to_pool.py`:
```python
"""把旧 web/test_accounts（HTTP/script provider）配置迁移为静态池 + dynamic_script。"""
from __future__ import annotations

from typing import Any

_DROP_KEYS = {
    "api_base_url", "login_method", "login_path", "login_body", "token_jsonpath",
    "auth_header", "auth_scheme", "create_method", "create_path", "create_body",
    "user_id_jsonpath", "cleanup_method", "cleanup_path", "timeout_seconds",
    "provider", "shared_username", "shared_password", "prepare_script",
    "cleanup_script", "script_config", "auto_cleanup",
}


def build_pool_from_legacy(legacy: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """从旧配置键值构造 (accounts, dynamic_script)。password 保持已加密原值。"""
    pool: list[dict[str, Any]] = []
    username = str(legacy.get("shared_username") or "").strip()
    password = legacy.get("shared_password")
    if username and password:
        pool.append({
            "label": "共享账号", "username": username, "password": password,
            "state": "admin", "enabled": True,
        })
    dynamic = ""
    if str(legacy.get("provider") or "").strip().lower() == "script":
        dynamic = str(legacy.get("prepare_script") or "").strip()
    return pool, dynamic


def upgrade(session) -> None:
    from database.models import ConfigStore

    rows = (
        session.query(ConfigStore)
        .filter(ConfigStore.category == "web", ConfigStore.config_group == "test_accounts")
        .all()
    )
    by_project: dict[int, dict[str, Any]] = {}
    for r in rows:
        by_project.setdefault(r.project_id, {})[str(r.config_key)] = r.config_value
    for project_id, legacy in by_project.items():
        pool, dynamic = build_pool_from_legacy(legacy)
        # 删旧键
        for r in list(rows):
            if r.project_id == project_id and str(r.config_key) in _DROP_KEYS:
                session.delete(r)
        _upsert(session, project_id, "accounts", pool)
        _upsert(session, project_id, "dynamic_script", dynamic)
    session.commit()


def _upsert(session, project_id: int, key: str, value: Any) -> None:
    from database.models import ConfigStore

    row = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.project_id == project_id,
            ConfigStore.category == "web",
            ConfigStore.config_group == "test_accounts",
            ConfigStore.config_key == key,
        )
        .first()
    )
    if row is None:
        session.add(ConfigStore(
            project_id=project_id, category="web", config_group="test_accounts",
            config_key=key, config_value=value,
        ))
    else:
        row.config_value = value
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/migrations/test_migrate_test_accounts_to_pool.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add database/migrations/data_migrations/migrate_test_accounts_to_pool.py tests/migrations/test_migrate_test_accounts_to_pool.py
git commit -m "feat(migration): 旧账号工厂配置迁移为静态池+dynamic_script"
```

---

## Task 10: 端到端自测验证（平台自测项目改静态池）

**Files:**
- 无代码改动；配置 + 验证

- [ ] **Step 1: 给平台自测项目配置静态池**

通过配置 API 或直接写 `config_store`，为自测项目在 `web/test_accounts` 写入：
```json
"accounts": [{"label":"管理员","username":"demo_admin","password":"<enc>","state":"admin","enabled":true}]
```
（`<enc>` 用 `encode_test_account_secret("<demo_admin 真实密码>")`。）

- [ ] **Step 2: 确认 hack 已删仍能跑**

跑一条 Web 登录用例（`POST /api/run_test`，category=web）。
Expected: 报告不再卡 running；账号变量绑定为 demo_admin；**无 `账号工厂接口不可连接 / ReadTimeout`**；登录步骤按被测账号真实状态通过或给出真实断言结果。

- [ ] **Step 3: 破坏性用例（可选）**

若自测项目含"改密码/删除"破坏性用例：新增一个 workflow 脚本（`action=create` 时重置 demo_admin 密码为已知值），把 `dynamic_script` 指向它；或改用一次性账号。再跑一轮确认不再级联 401。

- [ ] **Step 4: 全量冒烟**

Run: `python -m pytest tests/services/test_accounts/ tests/migrations/test_migrate_test_accounts_to_pool.py -q` 且 `python -c "import server.main"`
Expected: 全绿、无导入错误。

- [ ] **Step 5: 提交（若有配置脚本产物）**

```bash
git commit --allow-empty -m "test: 平台自测项目改静态池端到端验证（通用化账号准备）"
```

---

## 自审记录

- **Spec 覆盖**：模型/配置/组件/运行时/reset-hack/迁移/错误/测试 各节均有对应 Task（1–10）。
- **profile→state** 映射在 Task 4 与"关键映射"表一致（shared_admin→admin、dynamic_active→normal、dynamic_disabled→disabled、dynamic_boundary→boundary、isolated_lock→locked）。
- **符号一致**：`resolve_account`/`validate_account_requirement`/`prepare_web_test_data`/`cleanup_web_test_accounts`/`load_account_sources`/`WebTestDataError` 在各 Task 中名字一致。
- **无占位符**：新代码均给出完整实现；移动类给出精确来源与目标。
- **已知待落实点**：Task 7 Step 2（config.py 对 `accounts` 数组内 password 逐元素加密/掩码）需在实现时确认现有 config 写入逻辑并按需补一个小函数——已在该 Task 内闭环，不留给后续。
