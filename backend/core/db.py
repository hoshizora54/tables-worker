from __future__ import annotations

import contextlib
from typing import Any, Dict, Iterable, List

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, Row

from backend.core.config import settings


_ENGINE: Engine | None = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    connect_args = {}
    if settings.DATABASE_URL.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    _ENGINE = create_engine(settings.DATABASE_URL, connect_args=connect_args, future=True)
    return _ENGINE


@contextlib.contextmanager
def get_connection():
    engine = get_engine()
    with engine.connect() as conn:
        yield conn


def list_tables() -> List[str]:
    engine = get_engine()
    insp = inspect(engine)
    return sorted(insp.get_table_names())


def get_table_columns(table_name: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    insp = inspect(engine)
    cols = []
    for col in insp.get_columns(table_name):
        cols.append(
            {
                "name": col.get("name"),
                "type": str(col.get("type")),
                "nullable": bool(col.get("nullable", True)),
                "default": col.get("default"),
            }
        )
    return cols


def get_row_count(table_name: str) -> int:
    with get_connection() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) AS c FROM \"{table_name}\""))
        row = result.first()
        return int(row[0]) if row else 0


def fetch_sample(table_name: str, limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        result = conn.execute(text(f"SELECT * FROM \"{table_name}\" LIMIT :lim"), {"lim": limit})
        rows: Iterable[Row] = result.fetchall()
        keys = result.keys()
        return [dict(zip(keys, r)) for r in rows]


def run_select(sql: str, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        result = conn.execute(text(sql), params or {})
        rows: Iterable[Row] = result.fetchall()
        keys = result.keys()
        return [dict(zip(keys, r)) for r in rows]


def run_execute(sql: str, params: Dict[str, Any] | None = None) -> None:
    engine = get_engine()
    with engine.begin() as conn:  # transactional execute
        conn.execute(text(sql), params or {})


def run_execute_rowcount(sql: str, params: Dict[str, Any] | None = None) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        try:
            rc = int(result.rowcount)
        except Exception:
            rc = 0
        return rc


