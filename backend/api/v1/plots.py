from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict

from backend.core.db import run_select
from backend.core import db as core_db
from backend.services.llm_service import generate_plot_spec_from_nl


router = APIRouter()


class NLPlotRequest(BaseModel):
    task: str
    table_hint: str | None = None


@router.post("/nlplot")
async def nl_plot(req: NLPlotRequest):
    """Генерирует JSON-спеку графика через LLM и подготавливает табличные данные под него."""
    try:
        spec = generate_plot_spec_from_nl(req.task, table_hint=req.table_hint)
        if not spec or not isinstance(spec, dict):
            spec = {}

        # Нормализация/подстановка значений по умолчанию
        tables = core_db.list_tables()
        table = (spec.get("table") or req.table_hint or (tables[0] if tables else ""))
        if not table or table not in tables:
            table = tables[0] if tables else ""
        if not table:
            raise HTTPException(status_code=400, detail="Нет таблиц для построения графика")

        chart_type = (spec.get("type") or "").lower()
        if chart_type not in ("bar", "line", "hist"):
            # Эвристика по тексту запроса
            tl = (req.task or "").lower()
            if any(k in tl for k in ["hist", "гист", "распределение"]):
                chart_type = "hist"
            else:
                chart_type = "bar"

        if chart_type in ("bar", "line"):
            x = spec.get("x")
            y = spec.get("y")
            agg = (spec.get("agg") or "count").lower()
            limit = int(spec.get("limit") or 50)
            if not x:
                # Подберём x из колонок: по вхождению в текст запроса, иначе первая текстовая, иначе первая колонка
                x = _pick_column_for_x(table, req)
            if not y and agg == "count":
                sql = (
                    f"SELECT \"{x}\" AS x, COUNT(*) AS y FROM \"{table}\" GROUP BY \"{x}\" ORDER BY y DESC LIMIT :lim"
                )
                rows = run_select(sql, params={"lim": limit})
                return {"spec": spec, "rows": rows}
            if y:
                if agg == "mean":
                    sql = f"SELECT \"{x}\" AS x, AVG(\"{y}\") AS y FROM \"{table}\" GROUP BY \"{x}\" ORDER BY y DESC LIMIT :lim"
                elif agg == "sum":
                    sql = f"SELECT \"{x}\" AS x, SUM(\"{y}\") AS y FROM \"{table}\" GROUP BY \"{x}\" ORDER BY y DESC LIMIT :lim"
                elif agg == "min":
                    sql = f"SELECT \"{x}\" AS x, MIN(\"{y}\") AS y FROM \"{table}\" GROUP BY \"{x}\" ORDER BY y DESC LIMIT :lim"
                elif agg == "max":
                    sql = f"SELECT \"{x}\" AS x, MAX(\"{y}\") AS y FROM \"{table}\" GROUP BY \"{x}\" ORDER BY y DESC LIMIT :lim"
                else:  # default count
                    sql = f"SELECT \"{x}\" AS x, COUNT(\"{y}\") AS y FROM \"{table}\" GROUP BY \"{x}\" ORDER BY y DESC LIMIT :lim"
                rows = run_select(sql, params={"lim": limit})
                return {"spec": spec, "rows": rows}
            # некорректная спека — фоллбек на value_counts
            spec_fb, rows_fb = _fallback_value_counts(req, prefer_table=table)
            return {"spec": spec_fb, "rows": rows_fb}

        if chart_type == "hist":
            x = spec.get("x")
            bins = int(spec.get("bins") or 20)
            if not x:
                # Выберем первую числовую колонку, иначе первую попавшуюся
                x = _pick_numeric_column(table) or _pick_any_column(table)
            # Для простоты: отрисуем "сырые" значения (биннинг можно сделать на клиенте)
            rows = run_select(f"SELECT \"{x}\" AS x FROM \"{table}\" LIMIT 5000")
            return {"spec": spec, "rows": rows, "bins": bins}

        # Фоллбек на value_counts
        spec3, rows3 = _fallback_value_counts(req)
        return {"spec": spec3, "rows": rows3}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _fallback_value_counts(req: NLPlotRequest, *, prefer_table: str | None = None):
    # Выберем таблицу
    tables = core_db.list_tables()
    table = prefer_table or req.table_hint
    if table not in (tables or []):
        table = tables[0] if tables else None
    if not table:
        raise HTTPException(status_code=400, detail="Нет таблиц для построения графика")

    # Найдём колонку по вхождению имени в запрос
    task_lower = (req.task or "").lower()
    cand_col = None
    for c in core_db.get_table_columns(table):
        name = c.get("name") or ""
        if name and name.lower() in task_lower:
            cand_col = name
            break
    if not cand_col:
        # если не нашли — возьмём первую текстовую/категориальную колонку
        cols = core_db.get_table_columns(table)
        for c in cols:
            t = str(c.get("type", "")).upper()
            if any(x in t for x in ["CHAR", "TEXT", "STRING", "UUID", "DATE", "TIME"]):
                cand_col = c.get("name")
                break
        # если всё ещё нет — берём первую колонку вообще
        if not cand_col and cols:
            cand_col = cols[0].get("name")
    if not cand_col:
        raise HTTPException(status_code=400, detail="Не удалось определить колонку для графика")

    rows = run_select(
        f"SELECT \"{cand_col}\" AS x, COUNT(*) AS y FROM \"{table}\" GROUP BY \"{cand_col}\" ORDER BY y DESC LIMIT 50"
    )
    spec = {"type": "bar", "table": table, "x": cand_col, "agg": "count", "limit": 50}
    return spec, rows


def _pick_any_column(table: str) -> str | None:
    cols = core_db.get_table_columns(table)
    return cols[0].get("name") if cols else None


def _pick_numeric_column(table: str) -> str | None:
    for c in core_db.get_table_columns(table):
        t = str(c.get("type", "")).upper()
        if any(x in t for x in ["INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DEC"]):
            return c.get("name")
    return None


def _pick_column_for_x(table: str, req: NLPlotRequest) -> str:
    # 1) по вхождению в текст запроса
    task_lower = (req.task or "").lower()
    for c in core_db.get_table_columns(table):
        name = (c.get("name") or "").lower()
        if name and name in task_lower:
            return c.get("name")
    # 2) текстовая
    for c in core_db.get_table_columns(table):
        t = str(c.get("type", "")).upper()
        if any(x in t for x in ["CHAR", "TEXT", "STRING", "UUID", "DATE", "TIME"]):
            return c.get("name")
    # 3) любая
    any_col = _pick_any_column(table)
    if not any_col:
        raise HTTPException(status_code=400, detail="Нет колонок в таблице для графика")
    return any_col




