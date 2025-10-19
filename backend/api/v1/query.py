from __future__ import annotations

from typing import Any, Dict, Optional

from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.db import run_select


router = APIRouter()


class SQLRequest(BaseModel):
    sql: str
    params: Optional[Dict[str, Any]] = None


@router.post("/sql")
async def execute_sql(req: SQLRequest):
    sql_up = req.sql.strip().upper()
    if not (sql_up.startswith("SELECT") or sql_up.startswith("WITH")):
        raise HTTPException(status_code=400, detail="Разрешены только SELECT/WITH запросы")
    try:
        rows = run_select(req.sql, params=req.params)
        return {"rows": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class SQLBatchItem(BaseModel):
    sql: str
    params: Optional[Dict[str, Any]] = None


class SQLBatchRequest(BaseModel):
    items: list[SQLBatchItem]
    max_workers: int = 4


@router.post("/sql_batch")
async def execute_sql_batch(req: SQLBatchRequest):
    # Разрешаем только SELECT/WITH для каждого запроса
    for item in req.items:
        sql_up = item.sql.strip().upper()
        if not (sql_up.startswith("SELECT") or sql_up.startswith("WITH")):
            raise HTTPException(status_code=400, detail="Только SELECT/WITH в батче")

    results: list[Dict[str, Any]] = [None] * len(req.items)  # type: ignore
    with ThreadPoolExecutor(max_workers=min(max(req.max_workers, 1), 16)) as pool:
        futures = {pool.submit(run_select, it.sql, it.params or {}): idx for idx, it in enumerate(req.items)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                rows = fut.result()
                results[idx] = {"rows": rows, "count": len(rows)}
            except Exception as e:
                results[idx] = {"error": str(e)}
    return {"results": results}


