from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.llm_service import generate_sql_from_nl
from backend.core.db import run_select, run_execute_rowcount


router = APIRouter()


class NLQuery(BaseModel):
    task: str
    table_hint: Optional[str] = None


@router.post("/nl2sql")
async def nl_to_sql(req: NLQuery):
    try:
        sql = generate_sql_from_nl(req.task, table_hint=req.table_hint)
        return {"sql": sql}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/nlquery")
async def nl_query(req: NLQuery):
    try:
        sql = generate_sql_from_nl(req.task, table_hint=req.table_hint)
        rows = run_select(sql)
        return {"sql": sql, "rows": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class NLMultiQuery(BaseModel):
    task: str
    table_hint: Optional[str] = None


@router.post("/nlquery_multi")
async def nl_query_multi(req: NLMultiQuery):
    """Принимает задачу, в которой LLM может сгенерировать несколько SELECT, разделив их ';;'."""
    try:
        sql_blob = generate_sql_from_nl(req.task, table_hint=req.table_hint)
        # Разбивка на части: сначала ';;', затем по ';' с фильтрацией
        raw_parts = [p for p in sql_blob.replace('\n', ' ').split(';;')]
        parts = []
        for rp in raw_parts:
            parts += [p.strip() for p in rp.split(';') if p.strip()]
        outputs = []
        for p in parts:
            up = p.upper()
            if not (up.startswith("SELECT") or up.startswith("WITH")):
                outputs.append({"sql": p, "error": "Только SELECT/WITH"})
                continue
            try:
                rows = run_select(p)
                outputs.append({"sql": p, "count": len(rows), "rows": rows})
            except Exception as e:
                outputs.append({"sql": p, "error": str(e)})
        return {"queries": outputs}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class NLExecuteRequest(BaseModel):
    task: str
    table_hint: Optional[str] = None


@router.post("/execute")
async def nl_execute(req: NLExecuteRequest):
    """Выполнение нескольких SQL от LLM: поддержка SELECT/WITH и безопасных write-операций.
    Разделитель между запросами: ';;'. Для write возвращаем rowcount.
    """
    try:
        sql_blob = generate_sql_from_nl(req.task, table_hint=req.table_hint)
        # Разбивка на части (поддержка ';;' и ';')
        raw_parts = [p for p in sql_blob.replace('\n', ' ').split(';;')]
        parts: list[str] = []
        for rp in raw_parts:
            for p in rp.split(';'):
                p = p.strip()
                if p:
                    parts.append(p)
        outputs = []
        for p in parts:
            # Разбиваем внутри части, если LLM склеил несколько выражений
            inner = [s.strip() for s in p.split(';') if s.strip()]
            for stmt in inner or [p]:
                up = stmt.upper()
                if up.startswith("SELECT") or up.startswith("WITH"):
                    try:
                        rows = run_select(stmt)
                        outputs.append({"type": "read", "sql": stmt, "count": len(rows), "rows": rows})
                    except Exception as e:
                        outputs.append({"type": "read", "sql": stmt, "error": str(e)})
                    continue

                # Разрешим безопасные write: RENAME COLUMN и UPDATE c WHERE
                allowed = False
                if up.startswith("ALTER TABLE") and "RENAME COLUMN" in up:
                    allowed = True
                if up.startswith("UPDATE") and " WHERE " in up:
                    allowed = True
                if not allowed:
                    outputs.append({"type": "write", "sql": stmt, "error": "Запрос не разрешён политикой безопасности"})
                    continue

                try:
                    rc = run_execute_rowcount(stmt)
                    outputs.append({"type": "write", "sql": stmt, "rowcount": rc})
                except Exception as e:
                    outputs.append({"type": "write", "sql": stmt, "error": str(e)})

        return {"results": outputs, "raw": sql_blob}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


