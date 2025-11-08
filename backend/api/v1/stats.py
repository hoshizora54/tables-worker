from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.core.db import run_select

from math import ceil
from scipy import stats


router = APIRouter()


class TTestRequest(BaseModel):
    table: str
    group_col: str
    value_col: str
    group_a: str
    group_b: str
    where: Optional[str] = None
    equal_var: bool = False


@router.post("/ttest_ind")
async def ttest_independent(req: TTestRequest):
    try:
        where = f" WHERE {req.where} " if req.where else ""
        sql = (
            f'SELECT "{req.group_col}" AS grp, "{req.value_col}" AS val FROM "{req.table}"{where} '
            f'AND "{req.group_col}" IN (:a, :b)'
            if where
            else f'SELECT "{req.group_col}" AS grp, "{req.value_col}" AS val FROM "{req.table}" '
                 f'WHERE "{req.group_col}" IN (:a, :b)'
        )
        rows = run_select(sql, {"a": req.group_a, "b": req.group_b})
        vals_a = [r["val"] for r in rows if r["grp"] == req.group_a]
        vals_b = [r["val"] for r in rows if r["grp"] == req.group_b]
        if len(vals_a) < 2 or len(vals_b) < 2:
            raise HTTPException(status_code=400, detail="Недостаточно данных в группах")
        tstat, pval = stats.ttest_ind(vals_a, vals_b, equal_var=req.equal_var, nan_policy="omit")
        return {
            "t_stat": float(tstat),
            "p_value": float(pval),
            "n_a": len(vals_a),
            "n_b": len(vals_b),
            "mean_a": float(sum(vals_a) / len(vals_a)),
            "mean_b": float(sum(vals_b) / len(vals_b)),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class AnovaRequest(BaseModel):
    table: str
    group_col: str
    value_col: str
    where: Optional[str] = None


@router.post("/anova_oneway")
async def anova_one_way(req: AnovaRequest):
    try:
        where = f" WHERE {req.where} " if req.where else ""
        sql = f'SELECT "{req.group_col}" AS grp, "{req.value_col}" AS val FROM "{req.table}"{where}'
        rows = run_select(sql)
        groups: Dict[str, List[float]] = {}
        for r in rows:
            g = str(r["grp"]) if r["grp"] is not None else "__NA__"
            groups.setdefault(g, []).append(r["val"])
        valid = [vals for vals in groups.values() if len(vals) >= 2]
        if len(valid) < 2:
            raise HTTPException(status_code=400, detail="Недостаточно групп для ANOVA")
        fstat, pval = stats.f_oneway(*valid)
        summary = {k: {"n": len(v), "mean": (sum(v) / len(v) if v else None)} for k, v in groups.items()}
        return {"f_stat": float(fstat), "p_value": float(pval), "groups": summary}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class SampleSizeRequest(BaseModel):
    alpha: float = 0.05  # уровень значимости (двусторонний)
    power: float = 0.8   # мощность (1 - beta)
    effect_size_d: float  # ожидаемый эффект (Cohen's d)


@router.post("/sample_size_ttest")
async def sample_size_ttest(req: SampleSizeRequest):
    try:
        if req.effect_size_d <= 0:
            raise HTTPException(status_code=400, detail="effect_size_d должен быть > 0")
        z_alpha_over_2 = stats.norm.ppf(1 - req.alpha / 2)
        z_beta = stats.norm.ppf(req.power)
        n_per_group = 2 * ((z_alpha_over_2 + z_beta) ** 2) / (req.effect_size_d ** 2)
        n_per_group_int = int(ceil(n_per_group))
        return {"n_per_group": n_per_group_int, "total_n": n_per_group_int * 2}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))






