from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.core import db


router = APIRouter()


@router.get("/tables")
async def get_tables():
    return {"tables": db.list_tables()}


@router.get("/tables/{table}/columns")
async def get_columns(table: str):
    try:
        return {"columns": db.get_table_columns(table)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tables/{table}/count")
async def get_count(table: str):
    try:
        return {"count": db.get_row_count(table)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tables/{table}/sample")
async def get_sample(table: str, limit: int = Query(50, ge=1, le=1000)):
    try:
        return {"rows": db.fetch_sample(table, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tables/{table}/rename")
async def rename_column(table: str, old: str, new: str):
    try:
        # SQLite: ALTER TABLE ... RENAME COLUMN поддерживается в современных версиях.
        from backend.core.db import run_execute

        run_execute(f"ALTER TABLE \"{table}\" RENAME COLUMN \"{old}\" TO \"{new}\"")
        return {"status": "ok", "table": table, "old": old, "new": new}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


