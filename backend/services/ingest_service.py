from __future__ import annotations

import re
from typing import Literal

import pandas as pd
from sqlalchemy import text

from backend.core.db import get_connection


def _sanitize_table_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_")
    return cleaned or "table"


def save_dataframe_to_sql(
    df: pd.DataFrame,
    table_name: str,
    if_exists: Literal["replace", "append", "fail"] = "replace",
) -> str:
    actual_table = _sanitize_table_name(table_name)
    from backend.core.db import get_engine
    engine = get_engine()
    with engine.begin() as conn:
        df.to_sql(actual_table, con=conn, if_exists=if_exists, index=False)
        # Создадим простой индекс, если есть явный id
        if "id" in df.columns:
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{actual_table}_id ON \"{actual_table}\"(id)"))
            except Exception:
                pass
    return actual_table


