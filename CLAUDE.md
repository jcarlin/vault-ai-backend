# CLAUDE.md — Vault AI Backend

## Related Documentation

| File | Purpose | Read When |
|------|---------|-----------|
| `../CLAUDE.md` | Root project guide: architecture, all repos, tech stack, sprint status, key decisions | Understanding the full system, what ships when |
| `../ROADMAP.md` | Master product roadmap: 6 stages, 20 epics, all endpoints, effort estimates | Understanding what ships in which release |
| `vault-api-spec.md` | API endpoint specification: all endpoints (Rev 1–5), request/response formats, auth | Reviewing the API contract, seeing what's built vs planned |
| `PRD.md` | Full backend design: DB schema, training architecture, system design | Planning features beyond Rev 2 |
| `../vault-ai-frontend/CLAUDE.md` | Frontend components, API integration, pages, design tokens | Understanding how the frontend consumes this API |

## Current Scope: Epic 8 (Stage 3) — COMPLETE

**63 API endpoints + auth middleware + audit logging + CLI + Docker + 234 tests.** Everything else in `PRD.md` is future scope. Do not build features from later stages unless explicitly told to.

### What's done
- **Rev 1 (3 endpoints):** Chat streaming + non-streaming, models list, health check
- **Rev 2 (28 new endpoints):** Conversations CRUD, training jobs lifecycle, admin/users/keys/config, system metrics, insights analytics, activity feed
- **First-boot wizard (7 endpoints):** Setup status, network config, admin account + API key creation, TLS cert, model selection, system verification, setup completion with lockout
- **Epic 8 (24 new endpoints):** Full API gateway — audit log query/export/stats, full config GET/PUT, TLS GET/POST, text completions, embeddings, model detail, conversation export, expanded health, inference stats, services list/restart, logs, model management (list/detail/load/unload/active/import/delete), WebSocket live metrics
- API key auth middleware (Bearer token, SHA-256 hashed, SQLite storage)
- Admin scope enforcement on all `/vault/admin/*` and admin-only model management endpoints
- Request logging middleware (structured JSON via structlog + AuditLog table)
- Setup wizard middleware (unauthenticated access when pending, 404 when complete)
- `vault-admin` CLI (create-key, list-keys, revoke-key)
- Mock vLLM server for local dev without GPU (chat, completions, embeddings, models)
- Docker Compose stack (gateway + mock-vllm + Caddy reverse proxy)
- 234 tests (unit + integration), all passing
- Frontend (vault-ai-frontend) wired to all 31 Rev 1+2 endpoints — chat streaming, conversations, admin, settings, insights all using real API calls

### What's next
- Deploy on the Cube — GPU stack validated (vLLM via NGC container, CUDA 12.8, Driver 570) — swap mock for real vLLM
- End-to-end testing with real hardware
- Wire frontend to Epic 8 endpoints (model management UI, audit log viewer, expanded system health)
- Then: monitoring setup (Grafana/Cockpit), pilot deployment
- Stage 3 remaining: quarantine pipeline (Epic 9), update mechanism (Epic 10), support/diagnostics (Epic 11)

### Rev 1 Endpoints

```
POST /v1/chat/completions    → Proxy to vLLM with SSE streaming (industry-standard LLM API format)
GET  /v1/models              → List available models from local manifest file
GET  /vault/health           → System health check (vLLM status, GPU detection)
```

### Rev 2 Endpoints

```
Conversations:
GET    /vault/conversations              → List conversations (paginated, sorted by updatedAt)
POST   /vault/conversations              → Create conversation
GET    /vault/conversations/{id}         → Get conversation with messages
PUT    /vault/conversations/{id}         → Update title
DELETE /vault/conversations/{id}         → Delete conversation (cascades messages)
POST   /vault/conversations/{id}/messages → Add message to conversation

Training Jobs:
GET    /vault/training/jobs              → List training jobs
POST   /vault/training/jobs              → Create training job
GET    /vault/training/jobs/{id}         → Get job detail + metrics
POST   /vault/training/jobs/{id}/pause   → Pause running job
POST   /vault/training/jobs/{id}/resume  → Resume paused job
POST   /vault/training/jobs/{id}/cancel  → Cancel job
DELETE /vault/training/jobs/{id}         → Delete job record

Admin:
GET    /vault/admin/users                → List users
POST   /vault/admin/users                → Create user
PUT    /vault/admin/users/{id}           → Update user
DELETE /vault/admin/users/{id}           → Deactivate user (soft delete)
GET    /vault/admin/keys                 → List API keys
POST   /vault/admin/keys                 → Create API key
DELETE /vault/admin/keys/{id}            → Revoke API key
GET    /vault/admin/config/network       → Get network config
PUT    /vault/admin/config/network       → Update network config
GET    /vault/admin/config/system        → Get system settings
PUT    /vault/admin/config/system        → Update system settings

System & Analytics:
GET    /vault/system/resources           → CPU, RAM, disk, network metrics (psutil)
GET    /vault/system/gpu                 → Per-GPU metrics (py3nvml)
GET    /vault/insights?range=7d          → Usage analytics from audit log
GET    /vault/activity?limit=20          → Recent activity feed
```

### Epic 8 Endpoints (Full API Gateway)

```
Inference:
POST   /v1/completions                  → Text completion proxy to vLLM (streaming + non-streaming)
POST   /v1/embeddings                   → Embedding generation proxy to vLLM
GET    /v1/models/{model_id}            → Detailed single model info (manifest-enriched)

Conversations:
GET    /vault/conversations/{id}/export → Export conversation as JSON or Markdown

Audit & Config:
GET    /vault/admin/audit               → Query audit log with filters + pagination
GET    /vault/admin/audit/export        → Export audit log as CSV or JSON
GET    /vault/admin/audit/stats         → Aggregate stats (requests/user, tokens, model usage)
GET    /vault/admin/config              → Full merged config (network + system + TLS)
PUT    /vault/admin/config              → Update config with restart_required flag
GET    /vault/admin/config/tls          → TLS certificate info
POST   /vault/admin/config/tls         → Upload TLS cert + key (PEM validation)

System Monitoring:
GET    /vault/system/health             → Expanded health (all services + vLLM)
GET    /vault/system/inference          → Inference stats (RPM, latency, TPS from AuditLog)
GET    /vault/system/services           → List managed services (admin)
POST   /vault/system/services/{name}/restart → Restart service (admin, allowlisted)
GET    /vault/system/logs               → Paginated system logs (admin)

Model Management:
GET    /vault/models                    → List all models on disk with loaded/available status
GET    /vault/models/{model_id}         → Detailed model info (disk + manifest + loaded)
POST   /vault/models/{model_id}/load    → Load model → update gpu-config → restart vLLM (admin)
POST   /vault/models/{model_id}/unload  → Unload model from GPU (admin)
GET    /vault/models/active             → Currently loaded models + GPU allocation
POST   /vault/models/import             → Import model from path with validation (admin)
DELETE /vault/models/{model_id}         → Delete model from disk, refuses if loaded (admin)

WebSocket:
WS     /ws/system                       → Live system metrics push every 2s (token auth via query param)
```

### First-Boot Wizard Endpoints

```
GET  /vault/setup/status    → Setup state (pending/in_progress/complete)
POST /vault/setup/network   → Configure hostname, IP, DNS (hostnamectl/nmcli, no-ops on dev)
POST /vault/setup/admin     → Create admin user + API key (201, raw key shown once)
POST /vault/setup/tls       → Self-signed or custom TLS certificate (openssl, no-ops on dev)
POST /vault/setup/model     → Select default model from manifest
GET  /vault/setup/verify    → Run verification checks (DB, inference, GPU, TLS)
POST /vault/setup/complete  → Lock setup, write flag file, 404 all setup endpoints
```

**Middleware gating:** Setup endpoints are unauthenticated while setup is pending. After `POST /vault/setup/complete`, all `/vault/setup/*` endpoints return 404 — they cease to exist.

**State tracking:** Dual-layer — DB (`SystemConfig` table with `setup.*` keys) + flag file (`/opt/vault/data/.setup_complete`). In-memory cache via `app.state.setup_complete`.

### Rev 1 Non-API Tools

```
vault-admin create-key --label "Dr. Smith" --scope user     # Generate API key
vault-admin list-keys                                        # List keys (prefix only)
vault-admin revoke-key vault_sk_abc123...                    # Revoke key
```

### What Is NOT Current Scope

These are all real features in the roadmap but they ship in later stages:
- Real training execution (current: job records only, Axolotl/LoRA in Stage 5)
- File upload quarantine pipeline (Stage 3)
- JWT auth / LDAP/SSO (Stage 3-4, current uses API keys)
- GPU allocation API (Stage 5)
- PostgreSQL (current uses SQLite)
- Redis / Celery (current uses async tasks)
- Update mechanism (Stage 3)
- Documents & RAG pipeline (Stage 4)

---

## Development Setup

### Prerequisites
- Python 3.11+
- uv (package manager)

### Running Locally (No GPU)

```bash
# Install dependencies
uv sync

# Start mock vLLM server (returns fake streaming responses)
uvicorn tests.mocks.fake_vllm:app --port 8001

# Start the API gateway pointed at mock
VLLM_BASE_URL=http://localhost:8001 uvicorn app.main:app --reload --port 8000

# Create a test API key
python -m app.cli create-key --label "dev-test" --scope admin

# Test inference
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer vault_sk_..." \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5-32b-awq", "messages": [{"role": "user", "content": "Hello"}], "stream": true}'
```

### Running on the Cube (With GPU)

```bash
# vLLM is already running as a Docker container on the Cube
# Just point the gateway at it
VLLM_BASE_URL=http://localhost:8001 uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Running Tests

```bash
pytest                          # All tests
pytest tests/unit/              # Unit only
pytest tests/integration/       # Integration only
pytest -x -v                    # Stop on first failure, verbose
```

---

## Directory Structure

```
vault-ai-backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app entry, middleware registration
│   ├── config.py               # Pydantic settings (env vars)
│   ├── dependencies.py         # Dependency injection
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py       # Main router aggregator
│   │       ├── chat.py         # POST /v1/chat/completions, /v1/completions, /v1/embeddings
│   │       ├── models.py       # GET /v1/models, /v1/models/{model_id}
│   │       ├── health.py       # GET /vault/health
│   │       ├── conversations.py # Conversations CRUD + messages + export
│   │       ├── training.py     # Training jobs CRUD + lifecycle
│   │       ├── admin.py        # Users, API keys, config, TLS management
│   │       ├── audit.py        # Audit log query, export (CSV/JSON), stats
│   │       ├── system.py       # System resources, GPU, health, services, logs
│   │       ├── insights.py     # Usage analytics from audit log
│   │       ├── activity.py     # Recent activity feed
│   │       ├── model_management.py # /vault/models/* — load, unload, import, delete
│   │       ├── websocket.py    # WS /ws/system — live metrics push
│   │       └── setup.py        # First-boot wizard (7 endpoints)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── inference/
│   │   │   ├── __init__.py
│   │   │   ├── base.py         # Abstract InferenceBackend interface
│   │   │   └── vllm_client.py  # httpx async client to vLLM
│   │   ├── auth.py             # API key validation
│   │   ├── monitoring.py       # GPU metrics (py3nvml)
│   │   ├── conversations.py    # Conversation + message business logic + export
│   │   ├── training.py         # Training job management + state machine
│   │   ├── admin.py            # User/config/TLS management, wraps AuthService
│   │   ├── audit.py            # Audit log query, filtering, aggregation, CSV/JSON export
│   │   ├── system.py           # CPU/RAM/disk metrics via psutil
│   │   ├── service_manager.py  # Service status, restart, logs, inference stats
│   │   ├── model_manager.py    # Model lifecycle: disk scan, vLLM orchestration, import, delete
│   │   └── setup.py            # First-boot wizard logic, reuses AdminService
│   │
│   ├── schemas/                # Pydantic v2 request/response models
│   │   ├── __init__.py
│   │   ├── chat.py             # ChatCompletionRequest, ChatCompletionResponse
│   │   ├── models.py           # ModelInfo, ModelList
│   │   ├── health.py           # HealthResponse
│   │   ├── conversations.py    # ConversationCreate/Response, MessageCreate/Response
│   │   ├── training.py         # TrainingJobCreate/Response, TrainingConfig/Metrics
│   │   ├── admin.py            # UserCreate/Response, KeyCreate/Response, Config, TLS schemas
│   │   ├── audit.py            # AuditLogEntry, AuditLogResponse, AuditStatsResponse
│   │   ├── completions.py      # CompletionRequest, CompletionResponse
│   │   ├── embeddings.py       # EmbeddingRequest, EmbeddingResponse
│   │   ├── system.py           # SystemResources, GpuDetail
│   │   ├── services.py         # ServiceStatus, LogEntry, InferenceStatsResponse
│   │   ├── model_management.py # VaultModelInfo, ModelLoadResponse, ModelImportRequest
│   │   ├── insights.py         # InsightsResponse, UsageDataPoint, ModelUsageStats
│   │   ├── activity.py         # ActivityItem, ActivityFeed
│   │   └── setup.py            # Setup wizard request/response models
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── security.py         # API key hashing, validation
│   │   ├── exceptions.py       # Custom exception classes
│   │   ├── middleware.py        # Auth middleware, request logging + AuditLog, CORS
│   │   └── database.py         # SQLite + SQLAlchemy async: ApiKey, User, Conversation, Message, TrainingJob, AuditLog, SystemConfig
│   │
│   └── cli.py                  # vault-admin CLI tool (click or typer)
│
├── tests/
│   ├── conftest.py             # Fixtures: test client, mock vLLM, test DB
│   ├── mocks/
│   │   ├── fake_vllm.py        # Lightweight FastAPI app mimicking vLLM
│   │   └── fake_docker.py      # Mock Docker client for model management tests
│   ├── unit/
│   │   ├── test_auth.py
│   │   ├── test_schemas.py
│   │   ├── test_security.py
│   │   ├── test_exceptions.py
│   │   ├── test_inference_client.py
│   │   └── test_model_manager.py
│   └── integration/
│       ├── test_chat_endpoint.py
│       ├── test_models_endpoint.py
│       ├── test_health_endpoint.py
│       ├── test_conversations_endpoint.py
│       ├── test_completions_endpoint.py
│       ├── test_embeddings_endpoint.py
│       ├── test_training_endpoint.py
│       ├── test_admin_endpoint.py
│       ├── test_audit_endpoint.py
│       ├── test_system_endpoint.py
│       ├── test_system_monitoring_endpoint.py
│       ├── test_model_management_endpoint.py
│       ├── test_websocket_endpoint.py
│       ├── test_insights_endpoint.py
│       └── test_setup_endpoint.py
│
├── config/
│   ├── models.json             # Model manifest (installed models + metadata)
│   └── gpu-config.yaml         # GPU allocation strategy
│
├── docker/
│   ├── Dockerfile              # API gateway container
│   └── docker-compose.yml      # Full stack (gateway + vLLM + Caddy)
│
├── scripts/
│   └── health_check.sh         # Smoke test script
│
├── pyproject.toml              # uv/pip dependencies
├── vault-api-spec.md           # API endpoint specification (all revisions)
└── PRD.md                      # Full backend design reference (future scope)
```

### Directory Rules
- `app/api/` — Route handlers only. No business logic. Call services.
- `app/services/` — Business logic. No HTTP concerns. Testable in isolation.
- `app/schemas/` — Pydantic models only. No logic beyond validation.
- `app/core/` — Cross-cutting: auth, exceptions, middleware, database.
- `tests/mocks/` — Mock services for local dev without GPU.

---

## Coding Conventions

### Python Style

```python
# ALWAYS async — every endpoint, every service method, every DB call
async def get_models() -> list[ModelInfo]:
    ...

# NEVER synchronous def for anything that touches I/O
def get_models():  # ❌ WRONG
    ...
```

### Framework & Libraries

| Purpose | Use | Don't Use |
|---------|-----|-----------|
| HTTP framework | FastAPI | Flask, Django |
| HTTP client (to vLLM) | httpx (async) | requests, aiohttp |
| Schemas / validation | Pydantic v2 | dataclasses, attrs |
| ORM | SQLAlchemy 2.0 (async) | raw SQL, Tortoise, Peewee |
| Database (Rev 1) | SQLite via aiosqlite | PostgreSQL (later stage) |
| Task queue (Rev 1) | asyncio tasks | Celery, Redis (later stage) |
| CLI tool | typer or click | argparse |
| Testing | pytest + pytest-asyncio + httpx | unittest |
| Logging | structlog (JSON output) | print(), basic logging |

### API Key Auth

```python
# API keys: vault_sk_ prefix + 48 random chars
# Stored as SHA-256 hashes in SQLite
# Scopes: "user" (inference + read) and "admin" (everything)

# Auth header format:
# Authorization: Bearer vault_sk_a1b2c3d4e5f6...

# Middleware validates on every request except /vault/health and /vault/setup/* (when setup pending)
```

### Error Response Format

Every error follows this schema — no exceptions:

```json
{
  "error": {
    "code": "model_not_loaded",
    "message": "The requested model qwen2.5-32b-awq is not currently loaded.",
    "status": 503,
    "details": {
      "suggestion": "Contact your admin to load this model."
    }
  }
}
```

Use standard HTTP status codes: 400, 401, 403, 404, 409, 422, 429, 500, 503.

### Streaming (SSE)

```python
# Chat completions MUST support streaming via Server-Sent Events
# Use FastAPI StreamingResponse with httpx async streaming from vLLM

from fastapi.responses import StreamingResponse

async def stream_chat(request: ChatCompletionRequest):
    async def generate():
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", vllm_url, json=payload) as response:
                async for line in response.aiter_lines():
                    yield f"{line}\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### Request Logging

Every request is logged as structured JSON:

```json
{
  "timestamp": "2026-02-15T14:30:00Z",
  "method": "POST",
  "path": "/v1/chat/completions",
  "user_key_prefix": "vault_sk_a1b2",
  "model": "qwen2.5-32b-awq",
  "status": 200,
  "latency_ms": 1243,
  "tokens_input": 45,
  "tokens_output": 128
}
```

### Database (SQLAlchemy ORM)

```python
# ALWAYS use SQLAlchemy ORM — this enables PostgreSQL migration later
# Rev 1: SQLite via aiosqlite
# The ORM layer means swapping to PostgreSQL is a config change, not a rewrite

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Rev 1 connection
engine = create_async_engine("sqlite+aiosqlite:///data/vault.db")

# Later (Stage 3) — just change this line:
# engine = create_async_engine("postgresql+asyncpg://...")
```

### Inference Backend Interface

```python
# Adapter pattern — vLLM is the production backend, but the interface
# allows swapping for testing or future engines

class InferenceBackend(ABC):
    @abstractmethod
    async def chat_completion(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Stream chat completion chunks."""
        ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List available models."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if backend is responsive."""
        ...

class VLLMBackend(InferenceBackend):
    """Production backend — proxies to vLLM via httpx."""
    ...

class MockBackend(InferenceBackend):
    """Test backend — returns canned responses."""
    ...
```

---

## Environment Variables

```bash
# Required
VLLM_BASE_URL=http://localhost:8001     # vLLM server URL
VAULT_SECRET_KEY=<random-64-chars>      # For API key hashing

# Optional
VAULT_DB_URL=sqlite+aiosqlite:///data/vault.db   # Database URL
VAULT_LOG_LEVEL=info                              # Logging level
VAULT_MODELS_MANIFEST=/opt/vault/config/models.json  # Model manifest path
VAULT_CORS_ORIGINS=https://vault-cube.local       # Allowed CORS origins
VAULT_SETUP_FLAG_PATH=/opt/vault/data/.setup_complete  # Setup wizard completion flag
VAULT_TLS_CERT_DIR=/opt/vault/tls                      # TLS certificate directory
```

---

## Model Manifest (Rev 1)

Models are managed via a JSON file, not an API. Admin edits the file and restarts vLLM.

```json
{
  "models": [
    {
      "id": "qwen2.5-32b-awq",
      "name": "Qwen 2.5 32B (AWQ Quantized)",
      "path": "/opt/vault/models/qwen2.5-32b-awq",
      "parameters": "32B",
      "quantization": "AWQ 4-bit",
      "context_window": 32768,
      "vram_required_gb": 20,
      "description": "Best balance of capability and speed. Recommended for most use cases."
    },
    {
      "id": "llama-3.3-8b-q4",
      "name": "Llama 3.3 8B (4-bit)",
      "path": "/opt/vault/models/llama-3.3-8b-q4",
      "parameters": "8B",
      "quantization": "AWQ 4-bit",
      "context_window": 131072,
      "vram_required_gb": 6,
      "description": "Fast model for simple tasks. Lower capability but 4× faster."
    }
  ]
}
```

---

## Git Conventions

- Commit messages: 1–2 sentences max, keep it on one line. No multi-line bodies or bullet lists.
- Do not mention Claude, AI assistance, or co-authors in commit messages.
- Be concise and descriptive: `feat: add streaming SSE support to chat endpoint` not a paragraph.

---

## What Comes Next

See `vault-api-spec.md` for the full API endpoint specification, `PRD.md` for backend design, and `../ROADMAP.md` for staging. The next backend milestones are:

- **Stage 3 remaining:** Quarantine pipeline (Epic 9), update mechanism (Epic 10), support/diagnostics tooling (Epic 11)
- **Stage 4:** Documents & RAG, PII scanning, LDAP integration
- **Stage 5:** Training job API (Axolotl), LoRA adapter management, eval
- **Stage 6:** Developer mode, JupyterHub, multi-model serving

The architectural decisions made now (async, SQLAlchemy ORM, adapter pattern, structured logging) are specifically chosen to make these expansions straightforward.
