from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from sqlalchemy import text

from backend.core.db import get_engine, run_select


VERSIONS_TABLE = "__table_versions__"


def init_versioning_schema() -> None:
    """Создаёт служебную таблицу версий при отсутствии."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{VERSIONS_TABLE}" (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  table_name TEXT NOT NULL,
                  version_num INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  operation TEXT,
                  details TEXT,
                  snapshot_table TEXT NOT NULL
                );
                """
            )
        )
        conn.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_versions_unique ON \"{VERSIONS_TABLE}\"(table_name, version_num)"
            )
        )


def _now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _sanitize_table(name: str) -> str:
    # без агрессивной очистки: полагаемся на существующие имена
    return name.strip().strip('"')


def get_next_version_num(table_name: str) -> int:
    table_name = _sanitize_table(table_name)
    rows = run_select(
        f"SELECT MAX(version_num) AS mx FROM \"{VERSIONS_TABLE}\" WHERE table_name = :t",
        {"t": table_name},
    )
    mx = rows[0]["mx"] if rows and rows[0]["mx"] is not None else 0
    return int(mx) + 1


def snapshot_table(table_name: str, *, operation: str, details: str | None = None) -> Dict[str, Any]:
    """Делает снепшот текущего состояния таблицы в отдельную физическую таблицу и регистрирует версию."""
    engine = get_engine()
    t = _sanitize_table(table_name)
    v = get_next_version_num(t)
    snapshot = f"{t}__v{v}"
    with engine.begin() as conn:
        # Создаём таблицу-снепшот из текущего состояния
        conn.execute(text(f"DROP TABLE IF EXISTS \"{snapshot}\""))
        conn.execute(text(f"CREATE TABLE \"{snapshot}\" AS SELECT * FROM \"{t}\""))
        # Регистрируем версию
        conn.execute(
            text(
                f"""
                INSERT INTO "{VERSIONS_TABLE}"(table_name, version_num, created_at, operation, details, snapshot_table)
                VALUES (:t, :v, :ts, :op, :det, :snap)
                """
            ),
            {"t": t, "v": v, "ts": _now_iso(), "op": operation, "det": details or "", "snap": snapshot},
        )
    return {"table": t, "version": v, "snapshot_table": snapshot}


def list_versions(table_name: str) -> List[Dict[str, Any]]:
    t = _sanitize_table(table_name)
    rows = run_select(
        f"SELECT version_num, created_at, operation, details, snapshot_table FROM \"{VERSIONS_TABLE}\" WHERE table_name = :t ORDER BY version_num DESC",
        {"t": t},
    )
    return rows


def restore_version(table_name: str, version_num: int) -> Dict[str, Any]:
    """Полная замена текущей таблицы состоянием из снепшота версии.
    Примечание: индексы/констрейнты переопределяются, как в исходном MVP.
    """
    engine = get_engine()
    t = _sanitize_table(table_name)
    row = run_select(
        f"SELECT snapshot_table FROM \"{VERSIONS_TABLE}\" WHERE table_name = :t AND version_num = :v",
        {"t": t, "v": version_num},
    )
    if not row:
        raise ValueError("Версия не найдена")
    snapshot = row[0]["snapshot_table"]
    with engine.begin() as conn:
        # Переименуем текущую таблицу на случай отката после ошибки
        tmp_old = f"{t}__old__{int(dt.datetime.utcnow().timestamp())}"
        conn.execute(text(f"ALTER TABLE \"{t}\" RENAME TO \"{tmp_old}\""))
        try:
            conn.execute(text(f"CREATE TABLE \"{t}\" AS SELECT * FROM \"{snapshot}\""))
            conn.execute(text(f"DROP TABLE IF EXISTS \"{tmp_old}\""))
        except Exception:
            # восстановим исходное имя
            conn.execute(text(f"DROP TABLE IF EXISTS \"{t}\""))
            conn.execute(text(f"ALTER TABLE \"{tmp_old}\" RENAME TO \"{t}\""))
            raise
    return {"table": t, "restored_from": snapshot, "version": version_num}



