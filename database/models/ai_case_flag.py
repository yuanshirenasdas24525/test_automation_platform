"""AiCaseFlag —— AI 诊断结论落在用例上的可见标记 + 用户反馈记录。

设计（docs/ai_case_flags_design.md）：
  - 一条用例同时只有一个 active 标记（新诊断 supersede 旧的），历史保留；
  - 用户清除标记时必须给原因（cleared_reason），这就是反馈数据——
    下次 AI 诊断按用例注入 user_feedback，预检层对 wont_fix/更正为正常的
    用例直接跳过自动修复。
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from database.base import Base, JSONType


# ── flag_type ──────────────────────────────────────────────
AI_FLAG_MANUAL_FIX = "manual_fix"              # 需人工修改（AI 修不动的用例问题）
AI_FLAG_INTERFACE_DEFECT = "interface_defect"  # 疑似接口缺陷，重点检查
AI_FLAG_ENVIRONMENT = "environment"            # 环境/其他问题
AI_FLAG_AI_FIXED = "ai_fixed"                  # AI 已修复且验证通过，建议复核
ALL_AI_FLAG_TYPES = {
    AI_FLAG_MANUAL_FIX,
    AI_FLAG_INTERFACE_DEFECT,
    AI_FLAG_ENVIRONMENT,
    AI_FLAG_AI_FIXED,
}

# ── status ─────────────────────────────────────────────────
AI_FLAG_STATUS_ACTIVE = "active"
AI_FLAG_STATUS_CLEARED = "cleared"            # 人工清除（带反馈）
AI_FLAG_STATUS_AUTO_CLEARED = "auto_cleared"  # 后续执行通过 / 新诊断判正常
AI_FLAG_STATUS_SUPERSEDED = "superseded"      # 被同用例新标记覆盖
ALL_AI_FLAG_STATUSES = {
    AI_FLAG_STATUS_ACTIVE,
    AI_FLAG_STATUS_CLEARED,
    AI_FLAG_STATUS_AUTO_CLEARED,
    AI_FLAG_STATUS_SUPERSEDED,
}

# ── cleared_reason（清除即反馈，AI 学习信号）────────────────
AI_FLAG_REASON_MANUALLY_FIXED = "manually_fixed"    # 已人工修复（note 里"改了什么"喂给 AI 当经验）
AI_FLAG_REASON_MISJUDGED = "misjudged"              # AI 判断有误（配 corrected_classification）
AI_FLAG_REASON_EXTERNAL_FIXED = "external_fixed"    # 接口已修复 / 环境已恢复
AI_FLAG_REASON_WONT_FIX = "wont_fix"                # 无需处理（预期行为，如负向用例 4xx）
ALL_AI_FLAG_REASONS = {
    AI_FLAG_REASON_MANUALLY_FIXED,
    AI_FLAG_REASON_MISJUDGED,
    AI_FLAG_REASON_EXTERNAL_FIXED,
    AI_FLAG_REASON_WONT_FIX,
}


class AiCaseFlag(Base):
    __tablename__ = "ai_case_flags"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_id = Column(Integer, index=True)   # 冗余：列表/模块树按模块批查

    flag_type = Column(String(30), nullable=False)
    classification = Column(String(20))        # AI 原始分类（用例问题/接口问题/环境/正常）
    findings = Column(JSONType)                # list[str] 诊断发现（截断后的展示用）
    fix_rounds = Column(Integer, default=0)    # AI 尝试修复的轮数
    source_ai_run_id = Column(Integer, index=True)
    source_report_id = Column(Integer)

    status = Column(String(20), nullable=False, default=AI_FLAG_STATUS_ACTIVE, index=True)

    # —— 清除即反馈 ——
    cleared_at = Column(DateTime)
    cleared_by_id = Column(Integer)
    cleared_reason = Column(String(30))
    corrected_classification = Column(String(20))   # misjudged 时用户给的正确分类
    cleared_note = Column(Text)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
