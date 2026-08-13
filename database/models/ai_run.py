"""AI 任务运行记录。

每次调用 AI（生成用例 / 检查 / 总结报告等）都落一行，便于：
  - 异步任务状态查询（前端轮询）
  - token / 成本审计
  - 失败重跑（保留 input_payload）
  - 缓存命中（按 prompt_hash 找历史成功输出）

不直接放执行结果（生成的 case / requirements 等）—— 那些走对应 ORM 表，
这里只存"AI 调用本身"的元信息 + 原始 input/output JSON。
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Float, func
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# 任务状态枚举
AI_RUN_STATUS_PENDING = "pending"        # 已提交，Celery 还没拿
AI_RUN_STATUS_RUNNING = "running"        # 正在调 LLM
AI_RUN_STATUS_SUCCESS = "success"        # 完成，output 可读
AI_RUN_STATUS_FAILED = "failed"          # 调用失败 / 解析失败
AI_RUN_STATUS_CANCELLED = "cancelled"    # 用户主动取消（Celery revoke）
ALL_AI_RUN_STATUSES = {
    AI_RUN_STATUS_PENDING,
    AI_RUN_STATUS_RUNNING,
    AI_RUN_STATUS_SUCCESS,
    AI_RUN_STATUS_FAILED,
    AI_RUN_STATUS_CANCELLED,
}


# Feature 名常量 —— 必须跟 ai_gateway/prompts/<feature>.md 文件对应
AI_FEATURE_REQUIREMENT_PARSE = "requirement_parse"   # 需求文本 → 结构化需求点
AI_FEATURE_TEST_PLAN = "test_plan"                   # 需求 + 项目 → markdown 测试计划
AI_FEATURE_FUNCTIONAL_CASE_GEN = "functional_case_gen"  # 需求 → functional 用例批量
AI_FEATURE_FUNCTIONAL_CASE_ENHANCE = "functional_case_enhance"  # CLI Agent → 用例高级补全
AI_FEATURE_FUNCTIONAL_CASE_REVIEW = "functional_case_review"  # 检查现有 functional 用例质量
AI_FEATURE_API_CASE_GEN = "api_case_gen"             # OpenAPI → API 用例
AI_FEATURE_REPORT_SUMMARY = "report_summary"         # 报告 → 自然语言摘要
AI_FEATURE_TEST_RESULT_ANALYSIS = "test_result_analysis"  # 执行结果 → 用例体检建议
AI_FEATURE_FUNCTIONAL_TO_AUTO = "functional_to_auto"  # functional → web/app step
AI_FEATURE_WEB_UI_CASE_GEN = "web_ui_case_gen"        # 功能用例/元素库 → Web UI 用例草稿
AI_FEATURE_LOAD_PLAN_GEN = "load_plan_gen"           # OpenAPI → locust 压测脚本
AI_FEATURE_BUG_FIX = "bug_fix"                         # AI 一键修复 Bug
AI_FEATURE_API_REPORT_FIX = "api_report_fix"          # 报告级 AI 全面诊断 + 参数修复（异步）


class AiRun(Base):
    """AI 任务记录。"""
    __tablename__ = "ai_runs"

    id = Column(Integer, primary_key=True, index=True)
    # feature 见上面常量；不约束 enum，允许后续扩展不破坏老数据
    feature = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False, default=AI_RUN_STATUS_PENDING, index=True)

    # 关联：项目级任务挂 project_id；非项目级（比如未来有 global 任务）留 NULL
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    # Celery task_id：取消任务 / 状态查询时能反向找到 Celery 那边
    celery_task_id = Column(String(64), nullable=True, index=True)

    # 输入 / 输出 payload —— JSON 列；output 在 status=success 时填
    input_payload = Column(JSONType, nullable=True)
    output_payload = Column(JSONType, nullable=True)

    # 错误信息（status=failed/cancelled 时填）
    error = Column(Text, nullable=True)

    # 用的哪个 provider / model（审计用）
    provider = Column(String(40), nullable=True)
    model = Column(String(80), nullable=True)

    # token / 成本审计
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)

    # prompt 内容指纹 —— 缓存查询用 (hash(prompt + input))
    prompt_hash = Column(String(64), nullable=True, index=True)

    # prompt 模板版本 —— prompt 改了输出会变，方便对比 / 回归
    prompt_version = Column(String(20), nullable=True)

    # 用户 / 操作人（暂存字符串，后面接入认证再换 ForeignKey）
    operator = Column(String(80), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    project = relationship("Project")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "feature": self.feature,
            "status": self.status,
            "project_id": self.project_id,
            "celery_task_id": self.celery_task_id,
            "input_payload": self.input_payload,
            "output_payload": self.output_payload,
            "error": self.error,
            "provider": self.provider,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "prompt_hash": self.prompt_hash,
            "prompt_version": self.prompt_version,
            "operator": self.operator,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }
