from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import io
import pandas as pd

from backend.services.versioning_service import list_versions, restore_version
from backend.core.db import get_engine


router = APIRouter()


@router.get("/versions/{table}")
async def api_list_versions(table: str):
    try:
        return {"table": table, "versions": list_versions(table)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/versions/{table}/restore/{version}")
async def api_restore_version(table: str, version: int):
    try:
        res = restore_version(table, version)
        return {"status": "ok", **res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/versions/{table}/{version}/export/csv")
async def api_export_version_csv(table: str, version: int):
    try:
        vers = list_versions(table)
        snap = next((v["snapshot_table"] for v in vers if int(v["version_num"]) == int(version)), None)
        if not snap:
            raise HTTPException(status_code=404, detail="Версия не найдена")
        engine = get_engine()
        df = pd.read_sql(f'SELECT * FROM "{snap}"', con=engine)
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        filename = f"{table}__v{version}.csv"
        return StreamingResponse(
            buf, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/versions/{table}/{version}/export/xlsx")
async def api_export_version_xlsx(table: str, version: int):
    try:
        vers = list_versions(table)
        snap = next((v["snapshot_table"] for v in vers if int(v["version_num"]) == int(version)), None)
        if not snap:
            raise HTTPException(status_code=404, detail="Версия не найдена")
        engine = get_engine()
        df = pd.read_sql(f'SELECT * FROM "{snap}"', con=engine)
        xbuf = io.BytesIO()
        with pd.ExcelWriter(xbuf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=(table[:31] or "Sheet1"))
        xbuf.seek(0)
        filename = f"{table}__v{version}.xlsx"
        return StreamingResponse(
            xbuf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


