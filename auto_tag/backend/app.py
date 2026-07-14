"""FastAPI 应用入口。

在仓库根目录（含 auto_tag 的父目录）下执行::

    export PYTHONPATH=$PWD:$PYTHONPATH
    uvicorn auto_tag.backend.app:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auto_tag.backend.routers import database, duplicates, health, jobs, models, records
from auto_tag.constant import VERSION

import logging

logger = logging.getLogger(__name__)

app = FastAPI(title="auto_tag API", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(records.router, prefix="/api")
app.include_router(duplicates.router, prefix="/api")
app.include_router(database.router, prefix="/api")


@app.on_event("startup")
def _hydrate_vlm_endpoint_stats() -> None:
    """启动时从 work_dir/log 恢复 VLM 端点累计统计。"""
    try:
        from auto_tag.core.config import settings
        from auto_tag.core.vlm_endpoint_stats_store import hydrate_circuit_breaker_from_disk

        hydrate_circuit_breaker_from_disk(getattr(settings, "work_dir", None))
    except Exception:
        logger.exception("hydrate_vlm_endpoint_stats failed")
