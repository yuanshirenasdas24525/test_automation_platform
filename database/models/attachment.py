"""Attachment —— 需求附件（外链 OR 本地文件）。

设计：
  - 一条 Attachment 挂在一个 Requirement 上（删需求级联删附件）。
  - kind='link'：url 是外部 URL；不需要 size。
  - kind='file'：url 是平台静态目录下相对路径，落盘到 data/attachments/req_{rid}/。
  - 上传人记录在 uploaded_by_id，方便审计 / 谁加的链接。
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from database.base import Base


ATTACHMENT_KIND_LINK = "link"
ATTACHMENT_KIND_FILE = "file"
ALL_ATTACHMENT_KINDS = {ATTACHMENT_KIND_LINK, ATTACHMENT_KIND_FILE}


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 显示名（文件名或链接标题）
    name = Column(String(200), nullable=False)

    # 类型
    kind = Column(String(10), nullable=False)

    # URL：link → 外部 URL；file → /attachments/req_{rid}/{uuid}_{orig_name}
    url = Column(String(500), nullable=False)

    # 文件大小（字节），仅 file 类型有
    size_bytes = Column(Integer, nullable=True)

    uploaded_by_id = Column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    uploaded_by = relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "name": self.name,
            "kind": self.kind,
            "url": self.url,
            "size_bytes": self.size_bytes,
            "uploaded_by_id": self.uploaded_by_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
