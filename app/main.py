from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import v1_router
from app.config import settings
from app.core.database import close_db, init_db
from app.core.exceptions import VaultError, vault_error_handler
from app.core.middleware import AuthMiddleware, RequestLoggingMiddleware
from app.services.inference.vllm_client import VLLMBackend

_NAME_TO_LEVEL = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "warn": 30,
    "error": 40,
    "critical": 50,
}

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        _NAME_TO_LEVEL.get(settings.vault_log_level.lower(), 20)
    ),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    await init_db()

    # Create shared httpx client and inference backend
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)
    )
    backend = VLLMBackend(
        base_url=settings.vllm_base_url,
        http_client=http_client,
        api_key=settings.vllm_api_key,
    )
    app.state.inference_backend = backend

    # Check if first-boot setup has been completed (flag file = fast check, no DB)
    setup_flag = Path(settings.vault_setup_flag_path)
    app.state.setup_complete = setup_flag.exists()
    logger.info(
        "vault_backend_starting",
        vllm_url=settings.vllm_base_url,
        setup_complete=app.state.setup_complete,
    )
    yield

    await backend.close()
    await close_db()
    logger.info("vault_backend_stopping")


app = FastAPI(
    title="Vault AI Backend",
    description="API gateway for Vault Cube — self-hosted AI inference",
    version="0.1.0",
    lifespan=lifespan,
)

# Exception handler
app.add_exception_handler(VaultError, vault_error_handler)

# Middleware (order matters: outermost first)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.vault_cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(v1_router)


@app.get("/")
async def root():
    return {"service": "vault-ai-backend", "version": "0.1.0"}
