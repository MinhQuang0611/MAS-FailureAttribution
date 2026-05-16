"""
evaluation/evaluator.py

So sánh SQL sinh ra với Gold SQL.
Hỗ trợ hai metrics chính:
    - Execution Match (EM): kết quả thực thi có trùng không
    - Exact Set Match (ESM): cấu trúc SQL có khớp không (dạng normalized)
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Tuple


class Evaluator:
    """
    So sánh predicted SQL với gold SQL trên một SQLite database.
    """

    def __init__(self, db_dir: str | Path):
        """
        Args:
            db_dir: Thư mục chứa các file .sqlite,
                    cấu trúc: db_dir/<db_id>/<db_id>.sqlite
        """
        self.db_dir = Path(db_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execution_match(
        self,
        db_id: str,
        predicted_sql: str,
        gold_sql: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Thực thi cả hai SQL và so sánh kết quả dưới dạng set.

        Returns:
            (is_match, error_message)
        """
        db_path = self._get_db_path(db_id)
        if not db_path.exists():
            return False, f"Database not found: {db_path}"

        try:
            pred_rows = self._execute(db_path, predicted_sql)
            gold_rows = self._execute(db_path, gold_sql)
            match = set(map(tuple, pred_rows)) == set(map(tuple, gold_rows))
            return match, None
        except Exception as e:
            return False, str(e)

    def exact_set_match(
        self,
        predicted_sql: str,
        gold_sql: str,
    ) -> bool:
        """
        So sánh cấu trúc SQL ở dạng normalized (lowercase, strip whitespace).
        Đây là metric đơn giản — có thể mở rộng với AST-based comparison.
        """
        return self._normalize(predicted_sql) == self._normalize(gold_sql)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_db_path(self, db_id: str) -> Path:
        return self.db_dir / db_id / f"{db_id}.sqlite"

    def _execute(self, db_path: Path, sql: str) -> List[Any]:
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(sql)
            return cursor.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _normalize(sql: str) -> str:
        sql = sql.lower().strip()
        sql = re.sub(r"\s+", " ", sql)
        return sql
