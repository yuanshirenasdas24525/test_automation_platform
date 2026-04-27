from database.base import Base, JSONType
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship


# 项目支持的栈枚举。functional 是"人工功能用例"，没有自动化执行步骤。
# app 已废，跑 app_to_android 迁移脚本后剩下：
#   api / web / android / ios / functional
PROJECT_STACK_API = "api"
PROJECT_STACK_WEB = "web"
PROJECT_STACK_ANDROID = "android"
PROJECT_STACK_IOS = "ios"
PROJECT_STACK_FUNCTIONAL = "functional"
ALL_PROJECT_STACKS = {
    PROJECT_STACK_API,
    PROJECT_STACK_WEB,
    PROJECT_STACK_ANDROID,
    PROJECT_STACK_IOS,
    PROJECT_STACK_FUNCTIONAL,
}


class Project(Base):
    """业务项目。

    重构后项目不再绑定单一栈 —— 一个"电商"项目可以同时启用 API / Web /
    App / 功能用例四个执行视角，模块树跨栈共享。`enabled_stacks` 决定
    项目详情页里出现哪几个 Tab。

    迁移历史：
      v1: type 列单字符串（"API"/"Web"/"Mobile"），一个项目=一个栈
      v2 (2026-04-27): 改为 enabled_stacks JSON 列；type 列已通过
                       proj_stk_000001 迁移 drop 掉。
    """
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    icon = Column(String)
    # JSON 数组，元素 ⊆ ALL_PROJECT_STACKS。校验在 API 层做（pydantic）。
    # server_default 给 '["api"]' 是为了让历史脚本/外部工具直接 INSERT
    # projects 不传 enabled_stacks 时不会炸；正常业务路径都会显式传值。
    enabled_stacks = Column(
        JSONType,
        nullable=False,
        default=lambda: [PROJECT_STACK_API],
        server_default='["api"]',
    )
    sort_order = Column(Integer, default=0)
    modules = relationship("Module", back_populates="project", cascade="all, delete-orphan")
