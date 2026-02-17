from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import v1_router
from app.config import settings
from app.core.access_gate import AccessGateMiddleware
from app.core.database import ApiKey, async_session, close_db, init_db
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


async def _seed_admin_key() -> None:
    """Create a default admin API key if the DB has no active keys (cloud first-boot)."""
    from sqlalchemy import func, select

    from app.core.security import hash_api_key, get_key_prefix

    async with async_session() as session:
        count = await session.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.is_active == True))  # noqa: E712
        if count and count > 0:
            return

    # Use deterministic key from env var, or fall back to random generation
    raw_key = settings.vault_admin_api_key
    if raw_key:
        if not raw_key.startswith("vault_sk_") or len(raw_key) != 56:
            logger.error("invalid_vault_admin_api_key", hint="Must be vault_sk_ + 48 hex chars (56 total)")
            return
    else:
        from app.core.security import generate_api_key
        raw_key = generate_api_key()

    async with async_session() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="Cloud Admin (auto-generated)",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    if settings.vault_admin_api_key:
        logger.info("cloud_admin_key_seeded", prefix=get_key_prefix(raw_key), source="env")
    else:
        logger.warning(
            "cloud_admin_key_seeded",
            raw_key=raw_key,
            note="This key is shown ONCE at startup. Save it now — it cannot be retrieved later.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    await init_db()

    # Cloud mode: skip setup wizard, seed admin key
    if settings.vault_deployment_mode == "cloud":
        app.state.setup_complete = True
        await _seed_admin_key()
    else:
        # Check if first-boot setup has been completed (flag file = fast check, no DB)
        setup_flag = Path(settings.vault_setup_flag_path)
        app.state.setup_complete = setup_flag.exists()

    # Create shared httpx client and inference backend
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.vault_http_connect_timeout,
            read=settings.vault_http_read_timeout,
            write=5.0,
            pool=5.0,
        )
    )
    backend = VLLMBackend(
        base_url=settings.vllm_base_url,
        http_client=http_client,
        api_key=settings.vllm_api_key,
    )
    app.state.inference_backend = backend

    logger.info(
        "vault_backend_starting",
        vllm_url=settings.vllm_base_url,
        setup_complete=app.state.setup_complete,
        deployment_mode=settings.vault_deployment_mode,
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

# Middleware (Starlette: last-added = outermost. Execution order top to bottom.)
# 1. RequestLogging (outermost) — logs all requests including auth/gate rejections
# 2. CORS — handles preflight before auth
# 3. AccessGate — shared secret check (cloud mode only)
# 4. Auth — Bearer token validation (innermost)
app.add_middleware(AuthMiddleware)
app.add_middleware(AccessGateMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.vault_cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# Routes
app.include_router(v1_router)


@app.get("/")
async def root():
    return {"service": "vault-ai-backend", "version": "0.1.0"}
