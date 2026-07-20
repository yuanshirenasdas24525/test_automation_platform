"""平台 REST API 的 HTTP 客户端封装（MCP server 专用）。

鉴权策略（M1 固定 token 打通，API Key 机制是 M2）：
  - `TAP_TOKEN` 存在 → 直接当 Bearer token 用；
  - 否则用 `TAP_USERNAME` / `TAP_PASSWORD` 调 /api/auth/login 换 access_token，
    收到 401 时自动重新登录一次再重试（access_token 短期过期是常态）。

环境变量：
  - TAP_BASE_URL   平台地址，默认 http://127.0.0.1:54351
  - TAP_TOKEN      固定 access_token（可选）
  - TAP_USERNAME   平台账号（可选，与 TAP_PASSWORD 成对）
  - TAP_PASSWORD   平台密码
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:54351"


class PlatformApiError(RuntimeError):
    """平台返回业务错误或 HTTP 错误时抛出，message 面向 agent 可读。"""


class PlatformClient:
    """薄封装：负责鉴权、信封解包、错误转译，不含任何业务逻辑。"""

    def __init__(self) -> None:
        self.base_url = os.environ.get("TAP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self._fixed_token = os.environ.get("TAP_TOKEN") or None
        self._username = os.environ.get("TAP_USERNAME") or None
        self._password = os.environ.get("TAP_PASSWORD") or None
        self._access_token: str | None = self._fixed_token
        self._http = httpx.Client(base_url=self.base_url, timeout=60.0)

    # ------------------------------------------------------------------
    # 鉴权
    # ------------------------------------------------------------------
    def _login(self) -> None:
        if not (self._username and self._password):
            raise PlatformApiError(
                "未配置鉴权：请设置 TAP_TOKEN，或成对设置 TAP_USERNAME / TAP_PASSWORD"
            )
        resp = self._http.post(
            "/api/auth/login",
            json={"username": self._username, "password": self._password},
        )
        if resp.status_code != 200:
            raise PlatformApiError(
                f"平台登录失败（HTTP {resp.status_code}）：{resp.text[:200]}"
            )
        body = resp.json()
        token = (body.get("data") or {}).get("access_token")
        if not token:
            raise PlatformApiError(f"平台登录响应缺少 access_token：{str(body)[:200]}")
        self._access_token = token

    def _headers(self) -> dict[str, str]:
        if self._access_token is None:
            self._login()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # 请求 + 信封解包
    # ------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发请求并返回整个响应 JSON（信封 `{status, data?, message?, ...}`）。

        401 且具备账号密码时自动重登一次；业务 status=error 转成异常。
        """
        # 过滤掉 None 参数，避免 ?module_id=None 这类脏 query
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        resp = self._http.request(
            method, path, params=clean_params, json=json_body, headers=self._headers()
        )
        if resp.status_code == 401 and self._fixed_token is None:
            self._access_token = None  # 过期，重登后重试一次
            resp = self._http.request(
                method, path, params=clean_params, json=json_body, headers=self._headers()
            )

        if resp.status_code >= 400:
            # FastAPI 错误统一 {"detail": "..."}，取出来给 agent 一句人话
            detail: Any
            try:
                detail = resp.json().get("detail")
            except Exception:  # noqa: BLE001
                detail = resp.text[:200]
            raise PlatformApiError(f"平台接口 {method} {path} 失败（HTTP {resp.status_code}）：{detail}")

        body = resp.json()
        if isinstance(body, dict) and body.get("status") == "error":
            raise PlatformApiError(
                f"平台接口 {method} {path} 返回业务错误：{body.get('message')}"
            )
        return body

    def get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET 并直接返回信封里的 data 字段。"""
        return self.request("GET", path, params=params).get("data")
