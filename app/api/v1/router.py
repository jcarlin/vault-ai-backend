from fastapi import APIRouter

from app.api.v1.chat import router as chat_router
from app.api.v1.health import router as health_router
from app.api.v1.models import router as models_router

# Rev 2
from app.api.v1.activity import router as activity_router
from app.api.v1.admin import router as admin_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.insights import router as insights_router
from app.api.v1.setup import router as setup_router
from app.api.v1.system import router as system_router
from app.api.v1.training import router as training_router

# Epic 8
from app.api.v1.audit import router as audit_router
from app.api.v1.model_management import router as model_management_router
from app.api.v1.websocket import router as websocket_router

# Monitoring
from app.api.v1.metrics import router as metrics_router

# Epic 9
from app.api.v1.quarantine import router as quarantine_router

# Epic 11
from app.api.v1.diagnostics import router as diagnostics_router

# Epic 10
from app.api.v1.updates import router as updates_router

# Epic 14
from app.api.v1.auth import router as auth_router

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

# Epic 8
v1_router.include_router(audit_router, tags=["Audit"])
v1_router.include_router(model_management_router, tags=["Model Management"])

# Setup wizard
v1_router.include_router(setup_router, tags=["Setup"])

# WebSocket
v1_router.include_router(websocket_router, tags=["WebSocket"])

# Monitoring
v1_router.include_router(metrics_router, tags=["Metrics"])

# Epic 9
v1_router.include_router(quarantine_router, tags=["Quarantine"])

# Epic 11
v1_router.include_router(diagnostics_router, tags=["Diagnostics"])

# Epic 10
v1_router.include_router(updates_router, tags=["Updates"])

# Epic 14
v1_router.include_router(auth_router, tags=["Auth"])
