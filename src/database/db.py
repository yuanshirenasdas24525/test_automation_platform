# src/database/db.py
from src.database.engine import get_engine
from sqlalchemy.orm import sessionmaker
from src.database.sql_handler import SQLHandler


class DB:

    def __init__(self, db_conf: dict = None):

        if db_conf is None:
            db_conf = {
                'type': 'sqlite',
                'database': 'sqlite.db',
                'path': '/Users/Apple/Documents/test_automation_platform/data/db/sqlite.db'
            }
        engine = get_engine(db_conf)

        SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine
        )

        self.session = SessionLocal()
        self.sql = SQLHandler(self.session)

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.session.close()




