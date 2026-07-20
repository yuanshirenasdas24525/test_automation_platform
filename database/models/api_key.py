"""ApiKey —— 长效 service token（给 MCP server / CI 等机器调用方用）。

与用户 JWT 的区别：
  - 不过期刷新，长期有效（可设 expires_at）；
  - 权限走 scope 白名单（read / execute / ai），不是"登录了就全能"——
    校验逻辑在 server/api/auth.py::_check_api_key_scope；
  - 明文 key 只在签发时返回一次，库里只存 sha256 哈希（同 refresh token 策略）。

设计出处：docs/方案-MCP-server草案.md 第五、六节（M2 前置能力）。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func

from database.base import Base, JSONType

# scope 枚举：
#   read    —— 所有 GET 接口（敏感资源除外，见 auth._API_KEY_DENY_PREFIXES）
#   execute —— POST /api/run_test（触发自动化执行）
#   ai      —— 报告 AI 诊断 / 修复应用（默认 dry-run）两个 POST
API_KEY_SCOPE_READ = "read"
API_KEY_SCOPE_EXECUTE = "execute"
API_KEY_SCOPE_AI = "ai"
ALL_API_KEY_SCOPES = {API_KEY_SCOPE_READ, API_KEY_SCOPE_EXECUTE, API_KEY_SCOPE_AI}


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)               # 用途备注，如 "mcp-server"
    key_prefix = Column(String(16), nullable=False)          # 明文前缀（tap_xxxx），列表页辨认用
    key_hash = Column(String(64), nullable=False, unique=True, index=True)  # sha256(明文)

    scopes = Column(JSONType, nullable=False, default=list)  # ["read", "execute", ...]
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )  # 签发人；API Key 请求以该用户身份执行

    expires_at = Column(DateTime, nullable=True)             # 空 = 永不过期
    last_used_at = Column(DateTime, nullable=True)
    create_time = Column(DateTime, server_default=func.now())
