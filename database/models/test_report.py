from sqlalchemy import BigInteger, Column, Integer, String, DateTime, Float, func, ForeignKey
from database.base import Base



class TestReport(Base):
    __tablename__ = "test_reports"

    id = Column(Integer, primary_key=True, index=True)

    # 关联信息
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    category = Column(String, index=True)  # api, web, mobile (方便首页按类型统计)

    # 执行信息
    scene_name = Column(String)  # 场景名称或执行任务名称
    executor = Column(String, default="Admin")  # 执行人
    start_time = Column(DateTime, server_default=func.now())
    end_time = Column(DateTime)
    duration = Column(Float)  # 耗时（秒）

    # 统计数据 (核心：用于计算通过率)
    total_count = Column(Integer, default=0)  # 总用例数
    pass_count = Column(Integer, default=0)  # 成功数
    fail_count = Column(Integer, default=0)  # 失败数
    error_count = Column(Integer, default=0)  # 错误数（程序异常）
    skip_count = Column(Integer, default=0)  # 跳过数

    # 状态与结果
    status = Column(String)  # success, fail, running
    summary = Column(String)  # 简短的错误摘要

    # 详细数据 (可选)
    allure_url = Column(String)  # 如果集成了 Allure，存储报告链接
    context_session_id = Column(
        BigInteger,
        ForeignKey("ui_context_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 时间戳
    create_time = Column(DateTime, server_default=func.now())
