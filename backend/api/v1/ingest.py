from __future__ import annotations

import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.services.ingest_service import save_dataframe_to_sql


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
        return {"status": "ok", "table": actual_table, "rows": len(df.columns) and len(df)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


