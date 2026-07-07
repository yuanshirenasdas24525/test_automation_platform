"""UserSession —— 登录会话 / refresh token 存储。

refresh token 明文只在签发时返回给客户端，数据库仅保存哈希值。
这张表同时预留 Web、App、小程序等多端登录设备信息，供后续会话管理、
强制下线和安全审计使用。
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from database.base import Base


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_token_hash = Column(String(128), nullable=False, index=True)
    jti = Column(String(64), nullable=False, unique=True, index=True)

    session_kind = Column(String(32), nullable=False, default="password_login")
    client_type = Column(String(32), nullable=False, default="web")
    client_name = Column(String(128), nullable=True)
    app_version = Column(String(64), nullable=True)
    platform = Column(String(64), nullable=True)
    device_id = Column(String(128), nullable=True, index=True)
    device_name = Column(String(128), nullable=True)
    os_name = Column(String(64), nullable=True)
    os_version = Column(String(64), nullable=True)
    browser_name = Column(String(64), nullable=True)
    browser_version = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)

    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    revoked_reason = Column(String(64), nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="sessions")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "session_kind": self.session_kind,
            "client_type": self.client_type,
            "client_name": self.client_name,
            "app_version": self.app_version,
            "platform": self.platform,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "browser_name": self.browser_name,
            "browser_version": self.browser_version,
            "user_agent": self.user_agent,
            "ip_address": self.ip_address,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "revoked_reason": self.revoked_reason,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
