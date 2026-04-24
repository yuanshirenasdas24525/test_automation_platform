"""业务数据迁移脚本集合（与 Alembic schema 迁移分离）。

这里放的都是**一次性**、**不可回滚**的数据搬家脚本，
跑之前务必先做好数据库备份（pg_dump / mysqldump / cp xxx.db）。
"""
