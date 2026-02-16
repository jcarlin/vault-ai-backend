# Vault AI Backend

FastAPI gateway for the Vault Cube — proxies to vLLM (production) or Ollama (local dev).

## Setup

```bash
uv sync            # or: make install
```

## Run

```bash
# Local dev with Ollama (real LLM on Apple Silicon)
make dev

# Local dev with mock vLLM (canned responses, no LLM needed)
make mock

# Create an API key
make key
```

## Test

```bash
make test

# Smoke tests (replace with your key)
make health
make chat KEY=vault_sk_...
make models KEY=vault_sk_...
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_BASE_URL` | `http://localhost:8001` | Inference backend URL (vLLM, Ollama, RunPod) |
| `VLLM_API_KEY` | unset | Optional Bearer token for authenticated backends |
| `VAULT_SECRET_KEY` | `dev-secret-key-...` | Secret for API key hashing (change in prod) |
| `VAULT_DB_URL` | `sqlite+aiosqlite:///data/vault.db` | Database connection |
| `VAULT_LOG_LEVEL` | `info` | Log level |
| `VAULT_CORS_ORIGINS` | `https://vault-cube.local` | Allowed CORS origins |

## Makefile Commands

| Command | Description |
|---------|-------------|
| `make install` | Install dependencies via uv |
| `make dev` | Start backend → Ollama on :11434 |
| `make mock` | Start mock vLLM + backend (no LLM) |
| `make test` | Run all tests |
| `make key` | Create an admin API key |
| `make health` | Check /vault/health |
| `make chat KEY=...` | Streaming chat request |
| `make models KEY=...` | List models from backend |
