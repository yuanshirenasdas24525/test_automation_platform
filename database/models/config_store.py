from sqlalchemy import Column, Integer, String
from database.base import Base


class ConfigStore(Base):
    """配置中心键值对。

    所有配置项一律由用户管理 —— 可新增、可编辑、可删除。早期版本里有
    `is_system=True` 的"系统配置项"概念（启动时按 ini 五节自动 seed 且禁删），
    实际使用下来发现"凭空冒出来一堆不让删的项"对用户心智不友好，已彻底废弃：
      * 不再 seed，改成『推荐配置项』面板由前端按需『一键填入』；
      * 不再有删除限制；
      * `is_system` 列已通过 alembic 删除。
    """
    __tablename__ = "config_store"
    id = Column(Integer, primary_key=True)
    config_group = Column(String)
    config_key = Column(String)
    config_value = Column(String)
    category = Column(String)