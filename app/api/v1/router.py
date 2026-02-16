from fastapi import APIRouter

from app.api.v1.chat import router as chat_router
from app.api.v1.health import router as health_router
from app.api.v1.models import router as models_router

v1_router = APIRouter()

v1_router.include_router(chat_router, tags=["Chat"])
v1_router.include_router(models_router, tags=["Models"])
v1_router.include_router(health_router, tags=["Health"])
