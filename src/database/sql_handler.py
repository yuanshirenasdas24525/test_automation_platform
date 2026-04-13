# src/database/sql_handler.py
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Dict, Optional


class SQLHandler:

    def __init__(self, session: Session):
        self.session = session

    def query(self, sql: str, params: Optional[Dict] = None) -> List[Dict]:
        result = self.session.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]

    def execute(self, sql, params=None):
        try:
            self.session.execute(text(sql), params or {})
        except Exception:
            self.session.rollback()
            raise