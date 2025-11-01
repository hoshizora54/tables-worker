from fastapi import APIRouter

from backend.api.v1 import ingest, schema, query, plots, llm, versioning, analytics, stats


router = APIRouter()
router.include_router(ingest.router, prefix="/v1/ingest", tags=["ingest"])
router.include_router(schema.router, prefix="/v1/schema", tags=["schema"])
router.include_router(query.router, prefix="/v1/query", tags=["query"])
router.include_router(plots.router, prefix="/v1/plots", tags=["plots"])
router.include_router(llm.router, prefix="/v1/llm", tags=["llm"])
router.include_router(versioning.router, prefix="/v1/versioning", tags=["versioning"])
router.include_router(analytics.router, prefix="/v1/analytics", tags=["analytics"])
router.include_router(stats.router, prefix="/v1/stats", tags=["stats"]) 


