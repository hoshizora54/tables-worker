from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pandas as pd

from backend.core.db import run_select, run_execute, get_engine
from backend.services.versioning_service import snapshot_table


router = APIRouter()


class MovingAverageRequest(BaseModel):
    table: str
    value_col: str
    order_by: str
    window: int = 7
    partition_by: Optional[List[str]] = None
    limit: int = 1000


@router.post("/moving_average")
async def moving_average(req: MovingAverageRequest):
    try:
        part = (
            ("PARTITION BY " + ", ".join([f'"{p}"' for p in (req.partition_by or [])]))
            if req.partition_by
            else ""
        )
        sql = (
            f"SELECT *, AVG(\"{req.value_col}\") OVER ("
            f" {part} ORDER BY \"{req.order_by}\" ROWS BETWEEN {max(req.window-1, 0)} PRECEDING AND CURRENT ROW"
            f") AS moving_avg FROM \"{req.table}\" LIMIT :lim"
        )
        rows = run_select(sql, {"lim": req.limit})
        return {"rows": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class GroupByAggRequest(BaseModel):
    table: str
    group_by: List[str]
    # аггрегации: {"col": "sum" | "avg" | "min" | "max" | "count"}
    aggregations: Dict[str, str]
    having: Optional[str] = None
    order_by: Optional[str] = None
    limit: int = 1000


@router.post("/groupby")
async def groupby_agg(req: GroupByAggRequest):
    try:
        gb = ", ".join([f'"{g}"' for g in req.group_by])
        select_aggs: List[str] = []
        for col, fn in req.aggregations.items():
            fn_up = fn.lower()
            if fn_up not in ("sum", "avg", "min", "max", "count"):
                raise HTTPException(status_code=400, detail=f"Неподдерживаемая агрегация: {fn}")
            alias = f"{fn_up}_{col}"
            if fn_up == "count" and col == "*":
                select_aggs.append(f"COUNT(*) AS \"{alias}\"")
            else:
                select_aggs.append(f"{fn_up.upper()}(\"{col}\") AS \"{alias}\"")
        sel = ", ".join([gb] + select_aggs)
        sql = f"SELECT {sel} FROM \"{req.table}\" GROUP BY {gb}"
        if req.having:
            sql += f" HAVING {req.having}"
        if req.order_by:
            sql += f" ORDER BY {req.order_by}"
        sql += " LIMIT :lim"
        rows = run_select(sql, {"lim": req.limit})
        return {"rows": rows, "count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class FilterRequest(BaseModel):
    table: str
    where: Optional[str] = None
    order_by: Optional[str] = None
    limit: int = 1000


@router.post("/filter")
async def filter_rows(req: FilterRequest):
    try:
        where = f" WHERE {req.where} " if req.where else ""
        order = f" ORDER BY {req.order_by} " if req.order_by else ""
        sql = f"SELECT * FROM \"{req.table}\"{where}{order} LIMIT :lim"
        rows = run_select(sql, {"lim": req.limit})
        return {"rows": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class PivotRequest(BaseModel):
    table: str
    index: List[str]
    columns: str
    values: str
    aggfunc: str = "sum"  # sum/mean/min/max/count
    fill_value: Optional[float] = 0.0
    limit: int = 100000


@router.post("/pivot")
async def pivot(req: PivotRequest):
    try:
        engine = get_engine()
        df = pd.read_sql(f'SELECT * FROM "{req.table}" LIMIT {int(req.limit)}', con=engine)
        agg = req.aggfunc.lower()
        if agg not in ("sum", "mean", "min", "max", "count"):
            raise HTTPException(status_code=400, detail=f"Неподдерживаемая агрегация: {agg}")
        func = {"sum": "sum", "mean": "mean", "min": "min", "max": "max", "count": "count"}[agg]
        pvt = pd.pivot_table(df, index=req.index, columns=req.columns, values=req.values, aggfunc=func, fill_value=req.fill_value)
        pvt = pvt.reset_index()
        pvt.columns = [str(c) for c in pvt.columns]
        rows = pvt.to_dict(orient="records")
        return {"rows": rows, "count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class JoinRequest(BaseModel):
    left_table: str
    right_table: str
    left_on: str
    right_on: str
    how: str = "inner"  # inner/left
    new_table: Optional[str] = None


@router.post("/join")
async def join_tables(req: JoinRequest):
    try:
        how = req.how.lower()
        if how not in ("inner", "left"):
            raise HTTPException(status_code=400, detail="Поддерживаются только INNER и LEFT JOIN для SQLite")
        new_table = req.new_table or f"{req.left_table}__join__{req.right_table}"
        join_kw = "LEFT JOIN" if how == "left" else "INNER JOIN"
        sql_create = (
            f'SELECT l.*, r.* FROM "{req.left_table}" l {join_kw} "{req.right_table}" r ON l."{req.left_on}" = r."{req.right_on}"'
        )
        run_execute(f'DROP TABLE IF EXISTS "{new_table}"')
        run_execute(f'CREATE TABLE "{new_table}" AS {sql_create}')
        try:
            snapshot_table(new_table, operation="join", details=f"{req.left_table}.{req.left_on} {how} {req.right_table}.{req.right_on}")
        except Exception:
            pass
        rows = run_select(f'SELECT * FROM "{new_table}" LIMIT 50')
        return {"status": "ok", "table": new_table, "preview": rows}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



