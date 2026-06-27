"""FastAPI 应用入口。

在仓库根目录（含 auto_tag 的父目录）下执行::

    export PYTHONPATH=$PWD:$PYTHONPATH
    uvicorn auto_tag.backend.app:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auto_tag.backend.routers import database, duplicates, health, jobs, records

app = FastAPI(title="auto_tag API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(records.router, prefix="/api")
app.include_router(duplicates.router, prefix="/api")
app.include_router(database.router, prefix="/api")
