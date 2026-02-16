from fastapi import APIRouter

from app.api.v1.chat import router as chat_router
from app.api.v1.health import router as health_router
from app.api.v1.models import router as models_router

# Rev 2
from app.api.v1.activity import router as activity_router
from app.api.v1.admin import router as admin_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.insights import router as insights_router
from app.api.v1.system import router as system_router
from app.api.v1.training import router as training_router

v1_router = APIRouter()

# Rev 1
v1_router.include_router(chat_router, tags=["Chat"])
v1_router.include_router(models_router, tags=["Models"])
v1_router.include_router(health_router, tags=["Health"])

# Rev 2
v1_router.include_router(conversations_router, tags=["Conversations"])
v1_router.include_router(training_router, tags=["Training"])
v1_router.include_router(admin_router, tags=["Admin"])
v1_router.include_router(system_router, tags=["System"])
v1_router.include_router(insights_router, tags=["Insights"])
v1_router.include_router(activity_router, tags=["Activity"])
