"""盲点扫描（docs/盲点扫描报告_2026-07-10.md）修复项的回归测试。

覆盖可脱离 DB / 运行中服务、纯逻辑即可断言的安全修复：
  #1 脚本沙箱逃逸       utils/script_runtime.py
  #2 CLI 命令注入        server/services/bug_fix_service.py
  #4 JWT 默认密钥        server/api/auth.py
  #6 SVG 附件白名单      server/api/attachments.py
  #7 登录失败限速        server/api/auth.py
  #9 上传者不可伪造      server/api/attachments.py（白名单 + 入参层面）
  #10 CORS 默认收紧      server/main.py
  #5 对象级授权脚手架    server/api/authz.py

跑法：
    pytest tests/test_security_fixes.py -v

依赖缺失（fastapi / sqlalchemy 等）的用例会自动 skip，不会误报失败。
需要真实 DB / 起服务的端到端项（附件下载响应头、docker-compose 等）不在此文件内，
见报告答复里的手动验证清单。
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ===========================================================================
# #1 脚本运行隔离：项目脚本不在 API/Worker 进程内 exec
# ===========================================================================
class TestScriptIsolation:
    def _mod(self):
        return importlib.import_module("utils.script_runtime")

    def test_platform_package_is_not_importable(self):
        sr = self._mod()
        with pytest.raises(sr.ScriptRuntimeError, match="ModuleNotFoundError"):
            sr.run_script(
                "import server\ndef handler(input, vars=None, config=None): return server.__name__",
                kind="workflow",
            )

    def test_legit_script_runs(self):
        sr = self._mod()
        assert sr.run_script(
            "def handler(*a, **k):\n    return sum(a)",
            kind="function",
            args=[2, 3, 4],
        ) == 9

    def test_installed_import_works(self):
        sr = self._mod()
        code = "import json\ndef handler(body, config, vars=None):\n    return json.dumps(body)"
        out = sr.run_script(code, kind="crypto_response", body={"a": 1}, config={})
        assert out == '{"a": 1}'


# ===========================================================================
# #2 CLI 命令注入：prompt 永远是单一 argv 元素，shell 元字符不被解释
# ===========================================================================
class TestCommandInjection:
    def _agent(self, command):
        pytest.importorskip("sqlalchemy")
        mod = importlib.import_module("server.services.bug_fix_service")
        return mod.CliBugFixAgent(name="t", command=command)

    def test_prompt_stays_single_argv_element(self):
        agent = self._agent("claude -p '{{prompt}}'")
        malicious = "修复 $(rm -rf /); `whoami`; a & b | c"
        argv = agent._build_argv(malicious)
        assert argv[0] == "claude"
        assert argv[1] == "-p"
        assert argv[-1] == malicious  # 完整保留为一个参数，未被拆分/解释

    def test_empty_command_rejected(self):
        agent = self._agent("")
        with pytest.raises(ValueError):
            agent._build_argv("x")


# ===========================================================================
# #4 JWT：任何环境下缺失 / 默认 / 过短密钥都 fail-closed
# ===========================================================================
class TestJwtSecretKey:
    def _auth(self):
        pytest.importorskip("fastapi")
        pytest.importorskip("jose")
        return importlib.import_module("server.api.auth")

    def test_default_placeholder_rejected(self, monkeypatch):
        auth = self._auth()
        monkeypatch.setenv("JWT_SECRET_KEY", auth.DEFAULT_SECRET_KEY)
        with pytest.raises(RuntimeError):
            auth._resolve_secret_key()

    def test_short_key_rejected(self, monkeypatch):
        auth = self._auth()
        monkeypatch.setenv("JWT_SECRET_KEY", "too-short")
        with pytest.raises(RuntimeError):
            auth._resolve_secret_key()

    def test_strong_key_accepted(self, monkeypatch):
        auth = self._auth()
        strong = "s" * 40
        monkeypatch.setenv("JWT_SECRET_KEY", strong)
        assert auth._resolve_secret_key() == strong

    def test_missing_key_rejected(self, monkeypatch):
        auth = self._auth()
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        # 让配置中心也返回空，模拟"哪都没配"
        reload_config = importlib.import_module("utils.reload_config")
        monkeypatch.setattr(
            reload_config.config_center, "get", lambda *a, **k: "", raising=False
        )
        with pytest.raises(RuntimeError):
            auth._resolve_secret_key()


# ===========================================================================
# #7 登录失败限速：窗口内超阈值即锁定，成功清零
# ===========================================================================
class TestLoginThrottle:
    @pytest.fixture(autouse=True)
    def _force_throttle_on(self, monkeypatch):
        """限流已改为可配置（LOGIN_THROTTLE_ENABLED，测试环境常关）。

        这几条验证的是限流**功能本身**，必须显式打开，不能受本机 .env 影响 ——
        否则开发机把开关关掉后，这道安全测试会静默变成"永远通过不了"。
        """
        monkeypatch.setenv("LOGIN_THROTTLE_ENABLED", "1")

    def _auth(self):
        pytest.importorskip("fastapi")
        return importlib.import_module("server.api.auth")

    def test_lockout_after_max_failures(self):
        auth = self._auth()
        user, ip = "throttle_case_user", "203.0.113.9"
        auth._clear_login_failures(user, ip)
        assert auth._check_login_locked(user, ip) == 0
        for _ in range(auth.LOGIN_MAX_FAILURES):
            auth._register_login_failure(user, ip)
        assert auth._check_login_locked(user, ip) > 0
        auth._clear_login_failures(user, ip)  # 清理，避免污染其他用例

    def test_success_clears_counter(self):
        auth = self._auth()
        user, ip = "throttle_case_user2", "203.0.113.10"
        for _ in range(auth.LOGIN_MAX_FAILURES - 1):
            auth._register_login_failure(user, ip)
        auth._clear_login_failures(user, ip)  # 相当于登录成功
        assert auth._check_login_locked(user, ip) == 0

    def test_default_is_enabled(self, monkeypatch):
        """没配环境变量时必须是**开启**的 —— 安全默认值不能因为加了开关而反转。"""
        auth = self._auth()
        monkeypatch.delenv("LOGIN_THROTTLE_ENABLED", raising=False)
        assert auth._throttle_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "OFF"])
    def test_can_be_disabled_for_test_env(self, monkeypatch, val):
        """显式关闭后不再累计、不再锁定（测试环境跑回归用）。"""
        auth = self._auth()
        monkeypatch.setenv("LOGIN_THROTTLE_ENABLED", val)
        user, ip = "throttle_off_user", "203.0.113.11"
        auth._clear_login_failures(user, ip)
        for _ in range(auth.LOGIN_MAX_FAILURES * 2):
            auth._register_login_failure(user, ip)
        assert auth._check_login_locked(user, ip) == 0


class TestLoginSessionIsolation:
    """普通浏览器登录不能挤掉另一台浏览器中的长时间录制。"""

    def _auth(self):
        pytest.importorskip("fastapi")
        return importlib.import_module("server.api.auth")

    def test_web_login_only_revokes_same_device(self):
        auth = self._auth()
        same_device = SimpleNamespace(
            device_id="browser-a",
            revoked_at=None,
            revoked_reason=None,
        )
        recording_browser = SimpleNamespace(
            device_id="recorder-browser",
            revoked_at=None,
            revoked_reason=None,
        )
        db = SimpleNamespace(session=MagicMock())
        db.session.query.return_value.filter.return_value.all.return_value = [
            same_device,
            recording_browser,
        ]

        revoked = auth._revoke_same_client_type_sessions(
            db,
            user_id=1,
            client_type="web",
            device_id="browser-a",
            reason="replaced_by_new_login",
        )

        assert revoked == 1
        assert same_device.revoked_at is not None
        assert same_device.revoked_reason == "replaced_by_new_login"
        assert recording_browser.revoked_at is None

    def test_legacy_web_login_without_device_keeps_single_session_policy(self):
        auth = self._auth()
        sessions = [
            SimpleNamespace(device_id="browser-a", revoked_at=None, revoked_reason=None),
            SimpleNamespace(device_id="browser-b", revoked_at=None, revoked_reason=None),
        ]
        db = SimpleNamespace(session=MagicMock())
        db.session.query.return_value.filter.return_value.all.return_value = sessions

        revoked = auth._revoke_same_client_type_sessions(
            db,
            user_id=1,
            client_type="web",
            device_id=None,
            reason="replaced_by_new_login",
        )

        assert revoked == 2
        assert all(item.revoked_at is not None for item in sessions)


# ===========================================================================
# #6 SVG 附件白名单：.svg 不再允许（存储型 XSS 面）
# ===========================================================================
class TestAttachmentWhitelist:
    def _mod(self):
        pytest.importorskip("fastapi")
        return importlib.import_module("server.api.attachments")

    def test_svg_not_allowed(self):
        att = self._mod()
        assert ".svg" not in att.ALLOWED_EXTS

    def test_common_images_still_allowed(self):
        att = self._mod()
        assert {".png", ".jpg", ".webp"} <= att.ALLOWED_EXTS

    def test_executables_blocked(self):
        att = self._mod()
        assert {".exe", ".sh"} <= att.BLOCKED_EXTS


# ===========================================================================
# #10 CORS 默认收紧：未配置来源时不放开任何跨域
# ===========================================================================
class TestCorsDefault:
    def _main(self):
        pytest.importorskip("fastapi")
        return importlib.import_module("server.main")

    def test_default_closed(self, monkeypatch):
        main = self._main()
        monkeypatch.delenv("BACKEND_CORS_ORIGINS", raising=False)
        assert main._cors_origins() == []

    def test_explicit_wildcard_opt_in(self, monkeypatch):
        main = self._main()
        monkeypatch.setenv("BACKEND_CORS_ORIGINS", "*")
        assert main._cors_origins() == ["*"]

    def test_explicit_list(self, monkeypatch):
        main = self._main()
        monkeypatch.setenv("BACKEND_CORS_ORIGINS", "https://a.com, https://b.com")
        assert main._cors_origins() == ["https://a.com", "https://b.com"]


# ===========================================================================
# #5 对象级授权脚手架：当前放行所有成员，admin 恒通过，未登录/停用拒绝
# ===========================================================================
class _FakeRole:
    def __init__(self, code):
        self.code = code


class _FakeUser:
    def __init__(self, active=True, roles=()):
        self.is_active = active
        self.roles = [_FakeRole(r) for r in roles]


class TestProjectAuthz:
    def _authz(self):
        pytest.importorskip("fastapi")
        return importlib.import_module("server.api.authz")

    def test_active_member_allowed_today(self):
        authz = self._authz()
        # 现状：无成员表，所有成员访问所有项目
        assert authz.user_can_access_project(None, _FakeUser(roles=("test",)), 5) is True

    def test_admin_always_allowed_even_without_project(self):
        authz = self._authz()
        assert authz.user_can_access_project(None, _FakeUser(roles=("admin",)), None) is True

    def test_inactive_user_denied(self):
        authz = self._authz()
        assert authz.user_can_access_project(None, _FakeUser(active=False, roles=("admin",)), 5) is False

    def test_anonymous_denied(self):
        authz = self._authz()
        assert authz.user_can_access_project(None, None, 5) is False

    def test_non_admin_without_project_denied(self):
        authz = self._authz()
        assert authz.user_can_access_project(None, _FakeUser(roles=("dev",)), None) is False

    def test_assert_raises_403_when_denied(self):
        authz = self._authz()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            authz.assert_project_access(None, None, 5)
        assert exc.value.status_code == 403
