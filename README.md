# Vault AI Backend

FastAPI gateway for the Vault Cube — proxies to vLLM (production) or Ollama (local dev). Deploys to Cloud Run on merge to `main`.

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

## Cloud Run Deployment

Merging to `main` triggers a GitHub Actions workflow that builds the Docker image and deploys to Cloud Run.

**How it works:**
1. Builds `docker/Dockerfile.cloudrun`
2. Pushes to Artifact Registry (`us-central1-docker.pkg.dev/vault-ai-487703/vault-ai/vault-ai-backend`)
3. Deploys to Cloud Run (`vault-ai-backend` service, `us-central1`)

**Auth:** Workload Identity Federation (keyless OIDC) — no service account keys in the repo.

**Runtime secrets** (`VAULT_SECRET_KEY`, `VAULT_ACCESS_KEY`) are injected from GCP Secret Manager, not stored in the repo or workflow.

**Cloud mode differences:**
- `VAULT_DEPLOYMENT_MODE=cloud` — skips the first-boot setup wizard and auto-seeds an admin API key
- `VAULT_ACCESS_KEY` — shared secret gate via `X-Vault-Access-Key` header (disabled on Cube)

### GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `WIF_PROVIDER` | Workload Identity Federation provider resource name |
| `WIF_SERVICE_ACCOUNT` | GCP service account for deployment |
| `VLLM_BASE_URL` | Inference backend URL |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_BASE_URL` | `http://localhost:8001` | Inference backend URL (vLLM, Ollama, RunPod) |
| `VLLM_API_KEY` | unset | Optional Bearer token for authenticated backends |
| `VAULT_SECRET_KEY` | `dev-secret-key-...` | Secret for API key hashing (change in prod) |
| `VAULT_DB_URL` | `sqlite+aiosqlite:///data/vault.db` | Database connection |
| `VAULT_LOG_LEVEL` | `info` | Log level |
| `VAULT_CORS_ORIGINS` | `https://vault-cube.local` | Allowed CORS origins |
| `VAULT_DEPLOYMENT_MODE` | `cube` | `cube` (default) or `cloud` |
| `VAULT_ACCESS_KEY` | unset | Shared secret for cloud access gate (disabled when unset) |

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
