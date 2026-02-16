# --- Build stage ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-editable 2>/dev/null || uv sync --no-dev --no-editable

COPY app/ app/

# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r vault && useradd -r -g vault -s /bin/false vault

# Copy virtual env and app from builder
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/app /app/app
COPY config/ /app/config/

# Data directory for SQLite
RUN mkdir -p /app/data && chown vault:vault /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    VAULT_DB_URL=sqlite+aiosqlite:///app/data/vault.db \
    VAULT_MODELS_MANIFEST=/app/config/models.json

USER vault

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/vault/health'); exit(0 if r.status_code == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
