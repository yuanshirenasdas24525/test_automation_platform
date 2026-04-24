# src/database/db.py
from database.engine import get_engine
from sqlalchemy.orm import sessionmaker
from database.sql_handler import SQLHandler
from utils.read_conf import read_conf


class DB:

    def __init__(self, db_conf: dict = None):

        if db_conf is None:
            db_conf = read_conf.get_dict("sqlite_local")
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




