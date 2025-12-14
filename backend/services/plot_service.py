from __future__ import annotations

from typing import Dict, List, Optional
import io
import base64

import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

from backend.core.db import run_select


def value_counts(table: str, column: str, limit: int = 50) -> List[Dict[str, int]]:
    sql = (
        f"SELECT \"{column}\" AS value, COUNT(*) AS cnt FROM \"{table}\" "
        f"GROUP BY \"{column}\" ORDER BY cnt DESC, value ASC LIMIT :lim"
    )
    rows = run_select(sql, params={"lim": limit})
    return rows


def render_plot_to_base64(
    *,
    chart_type: str,
    data: pd.DataFrame,
    x: Optional[str] = None,
    y: Optional[str] = None,
    hue: Optional[str] = None,
    bins: int = 20,
    title: Optional[str] = None,
) -> str:
    chart = chart_type.lower()
    plt.figure(figsize=(8, 5))
    ax = plt.gca()
    if chart == "bar":
        # Ожидаем агрегированные данные с колонками x,y
        sns.barplot(data=data, x=x or "x", y=y or "y", ax=ax)
    elif chart == "line":
        sns.lineplot(data=data, x=x or "x", y=y or "y", hue=hue, marker="o", ax=ax)
    elif chart == "hist":
        sns.histplot(data=data, x=x or "x", bins=max(1, int(bins)), ax=ax, kde=False)
    elif chart == "scatter":
        sns.scatterplot(data=data, x=x or "x", y=y or "y", hue=hue, ax=ax)
    else:
        raise ValueError(f"Неизвестный тип графика: {chart_type}")
    if title:
        ax.set_title(title)
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=150)
    plt.close()
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return b64

