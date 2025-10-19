from __future__ import annotations

from typing import Dict, List

from backend.core.db import run_select


def value_counts(table: str, column: str, limit: int = 50) -> List[Dict[str, int]]:
    sql = (
        f"SELECT \"{column}\" AS value, COUNT(*) AS cnt FROM \"{table}\" "
        f"GROUP BY \"{column}\" ORDER BY cnt DESC, value ASC LIMIT :lim"
    )
    rows = run_select(sql, params={"lim": limit})
    return rows


