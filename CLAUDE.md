# CLAUDE.md — Vault AI Backend

## Current Scope: Rev 1 (Stage 2) — COMPLETE

**Rev 1 is complete: 3 API endpoints + auth middleware + request logging + CLI + Docker + 50 tests.** Everything else in `PRD.md` is future scope. Do not build features from later stages unless explicitly told to.

### What's done
- All 3 endpoints implemented and tested (chat streaming + non-streaming, models, health)
- API key auth middleware (Bearer token, SHA-256 hashed, SQLite storage)
- Request logging middleware (structured JSON via structlog)
- `vault-admin` CLI (create-key, list-keys, revoke-key)
- Mock vLLM server for local dev without GPU
- Docker Compose stack (gateway + mock-vllm + Caddy reverse proxy)
- Pre-commit hooks (gitleaks + secret detection)
- 50 tests (unit + integration), all passing

### What's next
- Connect frontend (vault-ai-prototype) to this backend
- Deploy on the Cube once GPU track completes (swap mock for real vLLM)
- End-to-end testing with real hardware
- Then: first-boot wizard, monitoring setup, pilot deployment

### Rev 1 Endpoints

```
POST /v1/chat/completions    → Proxy to vLLM with SSE streaming (industry-standard LLM API format)
GET  /v1/models              → List available models from local manifest file
GET  /vault/health           → System health check (vLLM status, GPU detection)
```

### Rev 1 Non-API Tools

```
vault-admin create-key --label "Dr. Smith" --scope user     # Generate API key
vault-admin list-keys                                        # List keys (prefix only)
vault-admin revoke-key vault_sk_abc123...                    # Revoke key
```

### What Is NOT Rev 1

These are all real features in the roadmap but they ship in later stages:
- Model load/unload via API (Rev 1: config file + service restart)
- Conversations persistence (Rev 1: localStorage in frontend)
- Training/fine-tuning (Stage 5)
- File upload quarantine pipeline (Stage 3)
- User management / JWT auth (Stage 3-4)
- GPU allocation API (Stage 5)
- PostgreSQL (Rev 1 uses SQLite)
- Redis / Celery (Rev 1 uses async tasks)

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
│   │       ├── chat.py         # POST /v1/chat/completions
│   │       ├── models.py       # GET /v1/models
│   │       └── health.py       # GET /vault/health
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── inference/
│   │   │   ├── __init__.py
│   │   │   ├── base.py         # Abstract InferenceBackend interface
│   │   │   └── vllm_client.py  # httpx async client to vLLM
│   │   ├── auth.py             # API key validation
│   │   └── monitoring.py       # GPU metrics (py3nvml)
│   │
│   ├── schemas/                # Pydantic v2 request/response models
│   │   ├── __init__.py
│   │   ├── chat.py             # ChatCompletionRequest, ChatCompletionResponse
│   │   ├── models.py           # ModelInfo, ModelList
│   │   └── health.py           # HealthResponse
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── security.py         # API key hashing, validation
│   │   ├── exceptions.py       # Custom exception classes
│   │   ├── middleware.py        # Auth middleware, request logging, CORS
│   │   └── database.py         # SQLite + SQLAlchemy async setup
│   │
│   └── cli.py                  # vault-admin CLI tool (click or typer)
│
├── tests/
│   ├── conftest.py             # Fixtures: test client, mock vLLM, test DB
│   ├── mocks/
│   │   └── fake_vllm.py        # Lightweight FastAPI app mimicking vLLM
│   ├── unit/
│   │   ├── test_auth.py
│   │   ├── test_schemas.py
│   │   └── test_inference_client.py
│   └── integration/
│       ├── test_chat_endpoint.py
│       ├── test_models_endpoint.py
│       └── test_health_endpoint.py
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

# Middleware validates on every request except /vault/health
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

## What Comes After Rev 1

See `PRD.md` for the full backend design and `ROADMAP.md` (root) for staging. The next backend milestones are:

- **Stage 3:** Expand to 57 endpoints — model management API, conversations API, admin/audit API, quarantine pipeline, update mechanism
- **Stage 4:** Documents & RAG, PII scanning, LDAP integration
- **Stage 5:** Training job API (Axolotl), LoRA adapter management, eval
- **Stage 6:** Developer mode, JupyterHub, multi-model serving

The architectural decisions made now (async, SQLAlchemy ORM, adapter pattern, structured logging) are specifically chosen to make these expansions straightforward.
