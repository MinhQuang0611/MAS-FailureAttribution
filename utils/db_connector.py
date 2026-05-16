"""
utils/db_connector.py

Kết nối an toàn với SQLite — hỗ trợ context manager và read-only mode.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, List, Optional


class DBConnector:
    """
    Context-manager wrapper cho SQLite connection.
    Mặc định mở ở chế độ read-only để tránh làm bẩn dữ liệu gốc.
    """

    def __init__(self, db_dir: str | Path):
        self.db_dir = Path(db_dir)

    def get_db_path(self, db_id: str) -> Path:
        return self.db_dir / db_id / f"{db_id}.sqlite"

    @contextmanager
    def connect(
        self, db_id: str, read_only: bool = True
    ) -> Generator[sqlite3.Connection, None, None]:
        """
        Usage:
            with connector.connect("concert_singer") as conn:
                rows = conn.execute("SELECT * FROM singer").fetchall()
        """
        db_path = self.get_db_path(db_id)
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        if read_only:
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            conn = sqlite3.connect(str(db_path))

        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def execute_query(
        self,
        db_id: str,
        sql: str,
        params: Optional[tuple] = None,
        read_only: bool = True,
    ) -> List[Any]:
        """
        Thực thi SQL và trả về danh sách rows.

        Returns:
            List of sqlite3.Row objects
        """
        with self.connect(db_id, read_only=read_only) as conn:
            cursor = conn.execute(sql, params or ())
            return cursor.fetchall()

    def get_schema(self, db_id: str) -> dict:
        """
        Trả về schema của database dưới dạng dict:
        {
            "table_name": [{"name": col_name, "type": col_type}, ...]
        }
        """
        schema = {}
        with self.connect(db_id) as conn:
            tables_query = "SELECT name FROM sqlite_master WHERE type='table'"
            tables = [row[0] for row in conn.execute(tables_query).fetchall()]
            for table in tables:
                cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
                schema[table] = [
                    {"name": col["name"], "type": col["type"]} for col in cols
                ]
        return schema

    def get_rich_schema(self, db_id: str) -> list[dict]:
        """
        Trả về schema chi tiết tương thích với định dạng TableSchema của agent:
        [
            {
                "table_name": str,
                "description": None,
                "columns": [{"name": str, "type": str, "nullable": bool, "comment": None}, ...],
                "foreign_keys": [{"column_name": str, "foreign_table": str, "foreign_column": str}, ...],
                "sample_rows": [dict, dict, dict]
            }, ...
        ]
        """
        rich_schema = []
        with self.connect(db_id) as conn:
            tables_query = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            tables = [row[0] for row in conn.execute(tables_query).fetchall()]
            
            for table in tables:
                # 1. Columns
                cols_raw = conn.execute(f"PRAGMA table_info(`{table}`)").fetchall()
                columns = [
                    {
                        "name": col["name"],
                        "type": col["type"],
                        "nullable": not bool(col["notnull"]),
                        "comment": None
                    }
                    for col in cols_raw
                ]
                
                # 2. Foreign keys
                fks_raw = conn.execute(f"PRAGMA foreign_key_list(`{table}`)").fetchall()
                foreign_keys = [
                    {
                        "column_name": fk["from"],
                        "foreign_table": fk["table"],
                        "foreign_column": fk["to"]
                    }
                    for fk in fks_raw
                ]
                
                # 3. Samples
                try:
                    samples_raw = conn.execute(f"SELECT * FROM `{table}` LIMIT 3").fetchall()
                    sample_rows = [dict(r) for r in samples_raw]
                except Exception:
                    sample_rows = []
                
                rich_schema.append({
                    "table_name": table,
                    "description": None,
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                    "sample_rows": sample_rows
                })
        return rich_schema
