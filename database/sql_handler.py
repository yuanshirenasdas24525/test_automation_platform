from __future__ import annotations

import re
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


class SQLHandler:

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _ensure_no_unresolved_vars(sql: str) -> None:
        leftovers = re.findall(r"\$\{[^}\n]+\}", sql or "")
        if leftovers:
            raise ValueError(
                "SQL 中存在未解析变量："
                f"{', '.join(leftovers)}。请确认变量已在环境变量、用例变量或前序提取中写入。"
            )

    def query(self, sql: str, params: Optional[Dict] = None) -> List[Dict]:
        self._ensure_no_unresolved_vars(sql)
        result = self.session.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]

    def fetchone(self, sql: str, params: Optional[Dict] = None):
        rows = self.query(sql, params)
        if not rows:
            return None
        row = rows[0]
        if len(row) == 1:
            return next(iter(row.values()))
        return row

    def execute(self, sql, params=None):
        try:
            self._ensure_no_unresolved_vars(sql)
            self.session.execute(text(sql), params or {})
        except Exception:
            self.session.rollback()
            raise
