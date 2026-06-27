import os

from fastapi import APIRouter

from auto_tag.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    parent = os.path.dirname(os.path.abspath(settings.db_path))
    return {
        "status": "ok",
        "embedding_store_path": settings.db_path,
        "chroma_path": settings.db_path,
        "embedding_parent_exists": os.path.isdir(parent),
        "chroma_parent_exists": os.path.isdir(parent),
        "collection": settings.collection_name,
    }
