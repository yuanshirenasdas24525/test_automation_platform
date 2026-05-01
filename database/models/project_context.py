"""项目上下文（Project Context）—— AI 的持久化"记忆层"。

存储从需求文档、项目资料、平台数据中提取的重要内容片段：
  - business_rule    — 业务规则 / 业务逻辑
  - data_model       — 数据模型 / 字段定义
  - api_contract     — API 契约 / 接口定义
  - architecture     — 架构决策 / 技术选型
  - term_definition  — 术语定义 / 名词解释
  - requirement      — 功能需求（冗余但方便检索）
  - constraint       — 约束条件
  - user_scenario    — 用户场景 / 用户故事
  - process_flow     — 业务流程 / 状态流转
  - dependency       — 依赖关系

每个上下文片段存储：内容原文 + AI 摘要 + 关键词 + 向量嵌入（pgvector，可选）。
"""
from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey, DateTime, Float,
    func, Index,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# -- 上下文字段类型枚举 -------------------------------------------------------
CONTEXT_TYPE_BUSINESS_RULE = "business_rule"
CONTEXT_TYPE_DATA_MODEL = "data_model"
CONTEXT_TYPE_API_CONTRACT = "api_contract"
CONTEXT_TYPE_ARCHITECTURE = "architecture"
CONTEXT_TYPE_TERM_DEFINITION = "term_definition"
CONTEXT_TYPE_REQUIREMENT = "requirement"
CONTEXT_TYPE_CONSTRAINT = "constraint"
CONTEXT_TYPE_USER_SCENARIO = "user_scenario"
CONTEXT_TYPE_PROCESS_FLOW = "process_flow"
CONTEXT_TYPE_DEPENDENCY = "dependency"

ALL_CONTEXT_TYPES = {
    CONTEXT_TYPE_BUSINESS_RULE,
    CONTEXT_TYPE_DATA_MODEL,
    CONTEXT_TYPE_API_CONTRACT,
    CONTEXT_TYPE_ARCHITECTURE,
    CONTEXT_TYPE_TERM_DEFINITION,
    CONTEXT_TYPE_REQUIREMENT,
    CONTEXT_TYPE_CONSTRAINT,
    CONTEXT_TYPE_USER_SCENARIO,
    CONTEXT_TYPE_PROCESS_FLOW,
    CONTEXT_TYPE_DEPENDENCY,
}

# source_type 枚举
CONTEXT_SOURCE_DOCUMENT = "document"
CONTEXT_SOURCE_MANUAL = "manual"
CONTEXT_SOURCE_API = "api_spec"
CONTEXT_SOURCE_ANALYSIS = "analysis"
CONTEXT_SOURCE_PLATFORM = "platform"


class ProjectContext(Base):
    """项目上下文片段表。"""
    __tablename__ = "project_contexts"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    module_id = Column(
        Integer, ForeignKey("modules.id"), nullable=True, index=True
    )

    # 来源
    source_type = Column(String(30), nullable=False, default=CONTEXT_SOURCE_DOCUMENT)
    source_file = Column(String(255), nullable=True)
    source_version = Column(Integer, default=1)

    # 核心内容
    context_type = Column(
        String(50), nullable=False, index=True
    )
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)          # 原文或提取后的内容
    summary = Column(Text, nullable=True)            # AI 摘要
    tags = Column(JSONType, default=list)
    keywords = Column(JSONType, default=list)

    # 向量嵌入 —— PostgreSQL 的 vector(1536) 类型
    # 如果数据库未安装 pgvector，此列会在 migration 中跳过
    embedding = Column(String, nullable=True)        # pgvector 不可用时用 JSON 字符串兜底

    # 元信息
    importance = Column(Integer, default=3)           # 1-5
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project = relationship("Project")
    module = relationship("Module")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "module_id": self.module_id,
            "source_type": self.source_type,
            "source_file": self.source_file,
            "source_version": self.source_version,
            "context_type": self.context_type,
            "title": self.title,
            "content": self.content,
            "summary": self.summary,
            "tags": self.tags or [],
            "keywords": self.keywords or [],
            "importance": self.importance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RequirementAnalysis(Base):
    """AI 需求分析的记录表 —— 记录每次 AI 分析任务的完整过程和匹配结果。"""
    __tablename__ = "requirement_analyses"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    ai_run_id = Column(
        Integer, ForeignKey("ai_runs.id"), nullable=True, index=True
    )

    # 来源文档
    document_name = Column(String(255), nullable=True)
    document_hash = Column(String(64), nullable=True)
    source_type = Column(String(20), nullable=False, default="text")  # text / file

    # 状态
    status = Column(String(20), nullable=False, default="pending", index=True)

    # 分析模式
    analysis_mode = Column(String(20), nullable=False, default="standard")

    # 分析结果
    analysis_result = Column(JSONType, nullable=True)
    new_requirement_ids = Column(JSONType, default=list)
    new_context_ids = Column(JSONType, default=list)
    matched_context_ids = Column(JSONType, default=list)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project")
    ai_run = relationship("AiRun")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "ai_run_id": self.ai_run_id,
            "document_name": self.document_name,
            "document_hash": self.document_hash,
            "source_type": self.source_type,
            "status": self.status,
            "analysis_mode": self.analysis_mode,
            "analysis_result": self.analysis_result,
            "new_requirement_ids": self.new_requirement_ids or [],
            "new_context_ids": self.new_context_ids or [],
            "matched_context_ids": self.matched_context_ids or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
