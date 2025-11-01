from __future__ import annotations

import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.services.ingest_service import save_dataframe_to_sql
from backend.services.versioning_service import snapshot_table
from backend.core.db import get_engine


router = APIRouter()


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    table_name: Optional[str] = Form(None),
    if_exists: str = Form("replace"),
):
    try:
        content = await file.read()
        filename = file.filename or "uploaded"
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif filename.lower().endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(status_code=400, detail="Поддерживаются только CSV и XLSX")

        name = table_name or (filename.rsplit(".", 1)[0])
        actual_table = save_dataframe_to_sql(df, name, if_exists=if_exists)
        try:
            snapshot_table(actual_table, operation="upload", details=f"if_exists={if_exists}")
        except Exception:
            # не блокируем загрузку, если снепшот не удался
            pass
        return {"status": "ok", "table": actual_table, "rows": len(df.columns) and len(df)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/csv")
async def export_csv(table: str):
    try:
        engine = get_engine()
        df = pd.read_sql(f'SELECT * FROM "{table}"', con=engine)
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        filename = f"{table}.csv"
        return StreamingResponse(
            buf, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/export/xlsx")
async def export_xlsx(table: str):
    try:
        engine = get_engine()
        df = pd.read_sql(f'SELECT * FROM "{table}"', con=engine)
        xbuf = io.BytesIO()
        with pd.ExcelWriter(xbuf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=table[:31] or "Sheet1")
        xbuf.seek(0)
        filename = f"{table}.xlsx"
        return StreamingResponse(
            xbuf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

