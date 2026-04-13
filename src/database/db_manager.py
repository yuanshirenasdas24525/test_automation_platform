# src/common/db_manager.py
# from src.database.db_engine import SQLHandlerFactory
# from src.utils.read_test_cases import read_conf
#
#
# class DBManager:
#     # 在这里统一配置，以后换数据库，只改这一行
#     _config = read_conf.get_dict("sqlite_local")
#
#     # 全项目共用这一个处理器（单例）
#     _handler = SQLHandlerFactory.create(_config)
#
#     @classmethod
#     def get_db(cls):
#         """其他文件统一通过这个方法获取连接"""
#         return cls._handler
#
#
# # 导出这个实例
# db = DBManager.get_db()