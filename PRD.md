# Vault AI Backend — Product Requirements Document

> **Scope note:** This document is the full backend design reference covering all planned stages. For what's being built *right now*, see `CLAUDE.md` in this directory (Rev 1: 3 endpoints). For the staged delivery plan, see `ROADMAP.md` at the project root.

---

## Executive Summary

**Product**: Self-hosted AI inference and fine-tuning platform for air-gapped enterprise deployments.

**Hardware Target**: 4× RTX 5090 (128GB total VRAM), Threadripper PRO 7975WX, 256GB ECC RAM, 8TB NVMe

**Timeline**: 3–6 months, solo dev + AI assistance

**Delivery model**: 6 stages. Rev 1 ships inference only (3 endpoints). Training, quarantine, updates, and advanced features layer in progressively.

---

## System Architecture

### Rev 1 (Stage 2)

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│   ┌──────────┐      ┌─────────────────┐                   │
│   │  Caddy   │──────│    FastAPI      │                   │
│   │  (TLS)   │      │    Gateway      │                   │
│   └──────────┘      │  (3 endpoints)  │                   │
│                      └────────┬────────┘                   │
│                               │                            │
│           Auth middleware (API keys, SQLite)                │
│           Request logging (structured JSON)                 │
│                               │                            │
│                               ▼                            │
│                      ┌──────────────┐                      │
│                      │    vLLM      │                      │
│                      │  (localhost)  │                      │
│                      └──────┬───────┘                      │
│                             │                              │
│   ┌────────────────────────────────────────────────────┐   │
│   │            GPU CLUSTER (4× RTX 5090)               │   │
│   │  Replica mode: 4 copies of 32B model               │   │
│   │  [GPU 0] [GPU 1] [GPU 2] [GPU 3]                  │   │
│   └────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

### Full Platform (Stage 3+)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│   ┌──────────┐      ┌─────────────────┐      ┌──────────────┐              │
│   │  Caddy   │──────│    FastAPI      │──────│  PostgreSQL  │              │
│   │  (TLS)   │      │    Gateway      │      │  (metadata)  │              │
│   └──────────┘      └────────┬────────┘      └──────────────┘              │
│                              │                                              │
│         ┌────────────────────┼────────────────────┐                        │
│         │                    │                    │                        │
│         ▼                    ▼                    ▼                        │
│   ┌───────────┐      ┌─────────────┐      ┌─────────────┐                 │
│   │   vLLM    │      │  Celery +   │      │ Monitoring  │                 │
│   │  Server   │      │  Axolotl    │      │  Service    │                 │
│   │           │      │  (training) │      │             │                 │
│   └─────┬─────┘      └──────┬──────┘      └──────┬──────┘                 │
│         │                   │                    │                        │
│         │            ┌──────┴──────┐             │                        │
│         │            │    Redis    │◄────────────┘                        │
│         │            │ (jobs/state)│                                      │
│         ▼            └─────────────┘                                      │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                    GPU CLUSTER (4× RTX 5090)                       │  │
│   │                                                                    │  │
│   │   [GPU 0-1: Inference]           [GPU 2-3: Training]              │  │
│   │   └─ vLLM tensor_parallel=2      └─ Axolotl LoRA/QLoRA           │  │
│   │   └─ Continuous batching         └─ Checkpoint management          │  │
│   │   └─ Dynamic model loading       └─ Job queue                      │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack — Staged Rollout

| Layer | Rev 1 (Stage 2) | Stage 3+ | Rationale |
|-------|-----------------|----------|-----------|
| **API Gateway** | FastAPI | — | Async-native, OpenAPI docs, SSE streaming |
| **Inference** | vLLM (replica mode) | + tensor parallel, multi-model | Continuous batching, PagedAttention, OpenAI-compatible |
| **Training** | — | Axolotl + Celery | LoRA/QLoRA, checkpoint management (Stage 5) |
| **Job Queue** | asyncio tasks | Celery + Redis | Redis added when training ships |
| **Database** | SQLite + SQLAlchemy ORM | PostgreSQL + Alembic | ORM enables swap without rewrite |
| **Auth** | API keys (hashed, SQLite) | JWT + LDAP/SSO | API keys sufficient for air-gapped Rev 1 |
| **Reverse Proxy** | Caddy | — | Simple config, self-signed TLS default, ACME when internet enabled |
| **Monitoring** | py3nvml + Prometheus | + Grafana dashboards | GPU metrics, alerting |

### Local Development Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Inference** | Mock vLLM server (FastAPI app returning fake SSE) | Dev without GPU hardware |
| **Database** | SQLite | Zero-config local dev |
| **Queue** | asyncio | Skip Redis for dev and tests |

---

## Inference Architecture (vLLM)

### Why vLLM, Not llama.cpp

| Feature | llama.cpp | vLLM |
|---------|-----------|------|
| Concurrent requests | Limited (requires external batching) | Continuous batching (100s concurrent) |
| Memory efficiency | Manual KV cache management | PagedAttention (2–4× more efficient) |
| Multi-GPU | Basic tensor splitting | Native tensor parallelism |
| Throughput | ~20–50 tok/s (single user) | ~100–500 tok/s (batched) |
| Quantization | Excellent (GGUF, Q4/Q5/Q6) | Good (AWQ, GPTQ, FP8) |
| Production scaling | Requires wrapper | Built for serving at scale |
| Best for | Edge devices, single-user, CPU inference | Multi-user GPU serving |

**Decision**: vLLM for production. Mock server for local dev/testing. No llama.cpp adapter needed — the mock is simpler and purpose-built.

### vLLM Configuration

```python
# Rev 1: Replica mode — 4 independent copies of a 32B model
# Each GPU runs its own vLLM instance (or vLLM handles replica routing)
engine_args = {
    "model": "Qwen/Qwen2.5-32B-Instruct-AWQ",
    "tensor_parallel_size": 1,   # Each replica on single GPU
    "gpu_memory_utilization": 0.90,
    "max_model_len": 32768,
    "enable_chunked_prefill": True,
}
```

```python
# Stage 5+: Tensor parallel mode for larger models (70B)
engine_args = {
    "model": "meta-llama/Llama-3.3-70B-Instruct",
    "tensor_parallel_size": 2,   # Split across 2 GPUs
    "gpu_memory_utilization": 0.90,
    "max_model_len": 8192,       # Reduced context for VRAM headroom
    "enable_chunked_prefill": True,
    "max_num_batched_tokens": 32768,
}
```

### Multi-GPU Reality on PCIe (4× RTX 5090)

These are not NVLink-connected GPUs. Inter-GPU communication goes through PCIe Gen 5 (~64 GB/s bidirectional vs 900 GB/s on NVLink H100s). This fundamentally shapes the parallelism strategy:

| Model Size | Strategy | Throughput vs Single GPU | Notes |
|-----------|----------|------------------------|-------|
| ≤32GB (32B-class at 4-bit) | 4× replicas | ~4× (near-linear) | **Sweet spot.** Zero inter-GPU comms. |
| 33–64GB (70B at 4-bit) | TP=2, 2 groups | ~2.5–3× | Workable. Context window may be limited. |
| 65–128GB (70B at higher precision) | TP=4 | ~1.5–2× | Possible but PCIe bottleneck hurts badly. |
| >128GB (400B+) | Can't run | — | Needs NVLink hardware. |

**Product recommendation:** Market and optimize for 32B-class models in replica mode. 70B is a capability, not the default. Never market TP=4 as a feature.

### Dynamic Model Loading (Stage 3+)

> **Rev 1:** Model is configured via `config/models.json` and loaded at vLLM startup. Changing models requires editing the config and restarting vLLM (30–90s downtime).

For Stage 3+, the system supports hot-swapping models without full restart:

1. **Model Registry**: Database table tracking available models, their requirements, and load state
2. **Unload Current**: Graceful drain of in-flight requests, then unload
3. **Load New**: Initialize vLLM engine with new model
4. **Health Check**: Verify model responds before marking ready

---

## Scaling Tiers

### Tier 1: 5–20 Concurrent Users (Rev 1 Target)

```
4× vLLM replicas (one per GPU), continuous batching
├── GPU 0-3: Inference (replica mode, all 4 GPUs)
├── Request queue: FastAPI async (in-process)
└── Expected throughput: 50–100 req/min
```

**What You Get**:
- Sub-2s time to first token for 32B model
- 100+ tokens/sec streaming per replica
- No request drops under normal load

### Tier 2: 20–100 Concurrent Users (Stage 3+)

```
Multiple vLLM workers + load balancer
├── 2× vLLM processes (each with tensor_parallel=2)
├── Redis request queue for overflow
├── Round-robin or least-connections LB
└── Expected throughput: 200–500 req/min
```

**Changes Required**:
- Add Redis-based request queue
- Implement vLLM worker pool manager
- Add health-check-based routing
- GPU allocation: configurable split between inference and training

### Tier 3: 100+ Concurrent Users (Future — Multi-Unit)

```
Horizontal scaling with orchestration
├── Multiple Vault Cubes on customer LAN
├── Centralized request routing
├── Shared PostgreSQL + Redis
└── Expected throughput: 1000+ req/min
```

This is essentially fleet management and is tracked in ROADMAP.md under "Future Considerations."

---

## Training Architecture (Stage 5)

> **Not Rev 1 scope.** Training ships in Stage 5 after inference is proven with pilot customers.

### Axolotl Integration

Axolotl provides production-ready fine-tuning with:
- LoRA/QLoRA for memory efficiency
- Checkpoint saving/resuming
- Multi-GPU training (DDP)
- Extensive model support

### Training Configuration

```yaml
# Example: QLoRA fine-tuning on 8B model
base_model: meta-llama/Llama-3.1-8B-Instruct
model_type: LlamaForCausalLM
load_in_4bit: true

adapter: lora
lora_r: 32
lora_alpha: 64
lora_dropout: 0.05
lora_target_modules:
  - q_proj
  - v_proj
  - k_proj
  - o_proj

dataset_format: alpaca
datasets:
  - path: /data/uploads/{job_id}/dataset.jsonl
    type: alpaca

output_dir: /data/checkpoints/{job_id}
num_epochs: 3
micro_batch_size: 4
gradient_accumulation_steps: 4
learning_rate: 2e-4
```

### Job Lifecycle

```
PENDING → QUEUED → PREPARING → TRAINING → FINALIZING → COMPLETED
                                  ↓
                              PAUSED (checkpoint saved)
                                  ↓
                              CANCELLED / FAILED
```

### GPU Allocation Strategy (Stage 5+)

| Mode | Inference GPUs | Training GPUs | Use Case |
|------|---------------|---------------|----------|
| All Inference (Rev 1 default) | 4 (all) | 0 | Normal operation, no training |
| Balanced | 2 (GPU 0-1) | 2 (GPU 2-3) | Normal operation with training |
| Inference Priority | 3 (GPU 0-2) | 1 (GPU 3) | High traffic + background training |
| Training Priority | 1 (GPU 0) | 3 (GPU 1-3) | Urgent fine-tuning job |

The allocation is set via API (Stage 5) or config file and requires:
1. Drain in-flight inference requests
2. Reconfigure vLLM engine
3. Update Celery worker GPU visibility

---

## API Specification

### Rev 1 Endpoints (Stage 2 — 3 endpoints)

```
POST /v1/chat/completions
  - OpenAI-compatible format (industry-standard)
  - SSE streaming response
  - Request body: { model, messages, temperature, max_tokens, stream }

GET  /v1/models
  - List available models from local manifest file
  - Response: { data: [{ id, object, owned_by }] }

GET  /vault/health
  - System health: vLLM status, GPU count, uptime
  - Response: { status, services: { vllm, gateway }, gpu_count, uptime_seconds }
```

### Stage 3 Additions — Model Management

```
GET    /vault/models                    # List all models on disk with status
GET    /vault/models/{model_id}         # Detailed model info
POST   /vault/models/{model_id}/load    # Load model to GPU (async, returns job ID)
POST   /vault/models/{model_id}/unload  # Unload from GPU
GET    /vault/models/active             # Currently loaded model(s)
POST   /vault/models/import             # Import model from USB/mounted drive
DELETE /vault/models/{model_id}         # Delete model from disk
```

### Stage 3 Additions — System Health & Monitoring

```
GET  /vault/system/health              # Detailed system + service status
GET  /vault/system/gpu                 # Per-GPU: utilization, VRAM, temp, power
GET  /vault/system/resources           # CPU, RAM, disk, uptime
GET  /vault/system/inference           # Requests/min, latency, tokens/sec, queue
GET  /vault/system/services            # Status of all managed services
POST /vault/system/services/{name}/restart  # Restart specific service
GET  /vault/system/logs                # Paginated logs, filterable
WS   /ws/system                        # Live system metrics push
```

### Stage 3 Additions — Conversations & History

```
GET    /vault/conversations            # List user's conversations (paginated)
POST   /vault/conversations            # Create conversation
GET    /vault/conversations/{id}       # Full conversation with messages
PUT    /vault/conversations/{id}       # Update metadata
DELETE /vault/conversations/{id}       # Delete
POST   /vault/conversations/{id}/messages  # Add message, trigger inference
GET    /vault/conversations/{id}/export    # Export as JSON or Markdown
```

### Stage 3 Additions — Administration

```
# API Key Management
GET    /vault/admin/keys               # List keys (prefix, label, scope)
POST   /vault/admin/keys               # Generate new key
PUT    /vault/admin/keys/{key_id}      # Update key metadata
DELETE /vault/admin/keys/{key_id}      # Revoke key

# Audit Log
GET    /vault/admin/audit              # Query audit log (filterable, paginated)
GET    /vault/admin/audit/export       # Export as CSV/JSON
GET    /vault/admin/audit/stats        # Aggregate stats

# System Configuration
GET    /vault/admin/config             # Current config
PUT    /vault/admin/config             # Update config
GET    /vault/admin/config/network     # Network settings
PUT    /vault/admin/config/network     # Update network
GET    /vault/admin/config/tls         # TLS cert info
POST   /vault/admin/config/tls        # Upload custom cert
```

### Stage 3 Additions — Updates & First-Boot

```
# Updates
GET  /vault/updates/status             # Current version, update history
POST /vault/updates/scan               # Scan USB for update bundle
GET  /vault/updates/pending            # Detected update details
POST /vault/updates/apply              # Apply update (returns job ID)
GET  /vault/updates/progress/{job_id}  # Update progress
POST /vault/updates/rollback           # Rollback to previous version
GET  /vault/updates/history            # Full update history

# First-Boot (unauthenticated, returns 404 after setup complete)
GET  /vault/setup/status               # Setup state
POST /vault/setup/network              # Configure hostname, IP
POST /vault/setup/admin                # Create admin account + first API key
POST /vault/setup/tls                  # TLS mode selection
POST /vault/setup/model                # Select default model
GET  /vault/setup/verify               # System verification
POST /vault/setup/complete             # Finalize, lock setup endpoints
```

### Stage 3 Additions — Quarantine

```
POST /vault/quarantine/scan            # Submit files for scanning
GET  /vault/quarantine/scan/{job_id}   # Scan progress and results
GET  /vault/quarantine/held            # Files in quarantine hold
GET  /vault/quarantine/held/{id}       # Held file details
POST /vault/quarantine/held/{id}/approve   # Admin override approve
POST /vault/quarantine/held/{id}/reject    # Reject and delete
GET  /vault/quarantine/signatures      # ClamAV/YARA signature versions
GET  /vault/quarantine/stats           # Aggregate scan stats
PUT  /vault/admin/config/quarantine    # Configure quarantine behavior
```

### Stage 4 Additions — Documents & RAG

```
POST   /vault/documents                # Upload document for indexing
GET    /vault/documents                # List documents with indexing status
GET    /vault/documents/{id}           # Document metadata
DELETE /vault/documents/{id}           # Remove document + index entries
POST   /vault/documents/search         # Semantic search across indexed docs
POST   /vault/collections              # Create named collection
GET    /vault/collections              # List collections
PUT    /vault/collections/{id}         # Update collection
```

### Stage 4 Additions — User Management (LDAP)

```
GET    /vault/admin/users              # List users
POST   /vault/admin/users              # Create user
PUT    /vault/admin/users/{id}         # Update permissions
DELETE /vault/admin/users/{id}         # Deactivate
```

### Stage 4 Additions — WebSockets

```
WS /api/ws/inference                   # Real-time inference stream
WS /api/ws/system                      # Live system metrics
WS /api/ws/logs                        # Live log streaming
WS /api/ws/updates                     # Update progress
```

### Stage 5 Additions — Training

```
POST   /vault/training/jobs            # Submit fine-tuning job
GET    /vault/training/jobs            # List all jobs
GET    /vault/training/jobs/{id}       # Job detail (progress, loss, ETA)
POST   /vault/training/jobs/{id}/cancel    # Cancel job
GET    /vault/training/adapters        # List trained LoRA adapters
POST   /vault/training/adapters/{id}/activate    # Load adapter onto model
POST   /vault/training/adapters/{id}/deactivate  # Remove adapter
DELETE /vault/training/adapters/{id}   # Delete adapter from disk
POST   /vault/training/validate        # Dry-run validation
```

### Stage 5 Additions — Evaluation

```
POST /vault/eval/jobs                  # Submit eval job
GET  /vault/eval/jobs                  # List eval jobs
GET  /vault/eval/jobs/{id}             # Eval results
GET  /vault/eval/compare               # Side-by-side model comparison
POST /vault/eval/quick                 # Quick synchronous eval
```

### Stage 6 Additions — Developer Mode

```
POST   /vault/admin/devmode/enable     # Enable with GPU allocation
POST   /vault/admin/devmode/disable    # Disable, reclaim GPUs
GET    /vault/admin/devmode/status     # State, GPU map, sessions
POST   /vault/admin/devmode/jupyter    # Launch JupyterHub
DELETE /vault/admin/devmode/jupyter    # Shut down JupyterHub
```

### Endpoint Count Summary

| Domain | Count | Stage | Priority |
|--------|-------|-------|----------|
| Inference (Industry-Standard API) | 2 | Rev 1 | Now |
| Health | 1 | Rev 1 | Now |
| Model Management | 7 | Stage 3 | Next |
| Conversations & History | 7 | Stage 3 | Next |
| System Health & Monitoring | 8 | Stage 3 | Next |
| Administration | 14 | Stage 3 | Next |
| Updates & Maintenance | 7 | Stage 3 | Next |
| First-Boot | 7 | Stage 3 | Next |
| Quarantine | 9 | Stage 3 | Next |
| Documents & RAG | 8 | Stage 4 | Later |
| User Management | 4 | Stage 4 | Later |
| WebSockets | 4 | Stage 4 | Later |
| Training & Fine-Tuning | 9 | Stage 5 | Later |
| Evaluation | 5 | Stage 5 | Later |
| Developer Mode | 5 | Stage 6 | Later |
| **Total** | **97** | | |

**Rev 1: 3 endpoints** → Stage 3: ~59 → Stage 4: +16 → Stage 5: +14 → Stage 6: +5

---

## Data Models

### Rev 1: SQLite Schema

```sql
-- API keys (Rev 1)
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,            -- UUID
    key_hash TEXT NOT NULL UNIQUE,  -- SHA-256 of full key
    key_prefix TEXT NOT NULL,       -- First 12 chars for display (vault_sk_a1b2...)
    label TEXT NOT NULL,            -- Human-readable label
    scope TEXT NOT NULL DEFAULT 'user',  -- 'user' or 'admin'
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    last_used_at TEXT
);
```

### Stage 3+: PostgreSQL Schema

```sql
-- Models registry
CREATE TABLE models (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    path VARCHAR(500) NOT NULL,
    size_bytes BIGINT,
    parameter_count BIGINT,
    quantization VARCHAR(50),
    is_loaded BOOLEAN DEFAULT FALSE,
    vram_required_mb INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Training jobs (Stage 5)
CREATE TABLE training_jobs (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    base_model_id UUID REFERENCES models(id),
    dataset_id UUID REFERENCES uploads(id),
    status VARCHAR(50) NOT NULL,
    config JSONB NOT NULL,
    metrics JSONB,
    checkpoint_path VARCHAR(500),
    gpu_ids INTEGER[],
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    created_by UUID REFERENCES users(id)
);

-- Uploaded files
CREATE TABLE uploads (
    id UUID PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    original_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100),
    size_bytes BIGINT,
    row_count INTEGER,
    path VARCHAR(500) NOT NULL,
    quarantine_status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW(),
    created_by UUID REFERENCES users(id)
);

-- Users (Stage 4 — LDAP integration)
CREATE TABLE users (
    id UUID PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Chat conversations (Stage 3)
CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    model_id UUID REFERENCES models(id),
    title VARCHAR(255),
    system_prompt TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id),
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    tokens_used INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Audit log (Stage 3)
CREATE TABLE audit_log (
    id UUID PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT NOW(),
    key_id TEXT,
    key_prefix TEXT,
    method VARCHAR(10),
    path TEXT,
    model TEXT,
    status_code INTEGER,
    latency_ms INTEGER,
    tokens_input INTEGER,
    tokens_output INTEGER
);
```

---

## Directory Structure

```
vault-ai-backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application entry
│   ├── config.py               # Pydantic settings (env vars)
│   ├── dependencies.py         # Dependency injection
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py       # Main router aggregator
│   │       ├── chat.py         # POST /v1/chat/completions      [Rev 1]
│   │       ├── models.py       # GET /v1/models                 [Rev 1]
│   │       └── health.py       # GET /vault/health              [Rev 1]
│   │       # --- Stage 3+ additions ---
│   │       # ├── model_mgmt.py   # /vault/models/*
│   │       # ├── conversations.py # /vault/conversations/*
│   │       # ├── admin.py        # /vault/admin/*
│   │       # ├── cluster.py      # /vault/system/*
│   │       # ├── uploads.py      # /vault/quarantine/*
│   │       # ├── updates.py      # /vault/updates/*
│   │       # └── setup.py        # /vault/setup/*
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── inference/
│   │   │   ├── __init__.py
│   │   │   ├── base.py         # Abstract InferenceBackend interface
│   │   │   └── vllm_client.py  # httpx async client to vLLM    [Rev 1]
│   │   ├── auth.py             # API key validation             [Rev 1]
│   │   └── monitoring.py       # GPU metrics (py3nvml)          [Rev 1 basic]
│   │   # --- Stage 3+ additions ---
│   │   # ├── training/
│   │   # │   ├── job_manager.py
│   │   # │   ├── axolotl_runner.py
│   │   # │   └── checkpoint.py
│   │   # ├── quarantine/
│   │   # │   ├── pipeline.py
│   │   # │   ├── clamav.py
│   │   # │   └── sanitizer.py
│   │   # └── files/
│   │   #     ├── upload_handler.py
│   │   #     └── parsers/
│   │
│   ├── schemas/                # Pydantic v2 request/response
│   │   ├── __init__.py
│   │   ├── chat.py             # [Rev 1]
│   │   ├── models.py           # [Rev 1]
│   │   └── health.py           # [Rev 1]
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── security.py         # API key hashing, validation    [Rev 1]
│   │   ├── exceptions.py       # Custom exception classes        [Rev 1]
│   │   ├── middleware.py        # CORS, auth, request logging    [Rev 1]
│   │   └── database.py         # SQLite + SQLAlchemy async      [Rev 1]
│   │
│   ├── cli.py                  # vault-admin CLI                 [Rev 1]
│   │
│   └── workers/                # Stage 5+
│       ├── __init__.py
│       └── celery_app.py
│
├── tests/
│   ├── conftest.py
│   ├── mocks/
│   │   └── fake_vllm.py        # Mock vLLM server for local dev
│   ├── unit/
│   └── integration/
│
├── config/
│   ├── models.json             # Model manifest
│   └── gpu-config.yaml         # GPU allocation
│
├── migrations/                 # Alembic (Stage 3+ with PostgreSQL)
│   ├── versions/
│   └── env.py
│
├── data/
│   ├── models/                 # Model weights (safetensors)
│   ├── uploads/                # User uploaded files
│   ├── checkpoints/            # Training checkpoints (Stage 5)
│   └── logs/                   # Application logs
│
├── docker/
│   ├── Dockerfile              # API gateway container
│   ├── Dockerfile.worker       # Celery worker (Stage 5)
│   └── docker-compose.yml      # Full stack
│
├── scripts/
│   ├── health_check.sh
│   ├── setup_airgap.sh         # Offline installation
│   ├── download_models.sh      # Pre-fetch model weights
│   └── backup.sh               # Database + checkpoints backup
│
├── pyproject.toml
├── CLAUDE.md                   # Backend coding guide + current scope
└── PRD.md                      # This file — full design reference
```

---

## Air-Gap Deployment

### Network Modes

Vault AI boxes ship **air-gapped by default** but are connected to the customer's internal LAN. Internet access is an optional customer choice.

| Mode | Default | Network | TLS | Model Source | Updates |
|------|---------|---------|-----|-------------|---------|
| **Air-gapped + LAN** | Yes | Customer LAN only, no internet | Self-signed cert or customer's internal CA | Pre-loaded on NVMe | USB drive or LAN file share |
| **Internet Enabled** | No | Customer LAN + internet | ACME auto-TLS via Caddy | Local + optional HuggingFace pull | Optional online update check |

**Air-gapped + LAN (default)**:
- All models, weights, and dependencies ship pre-loaded on the NVMe drives
- TLS uses a self-signed certificate (generated on first boot) or a certificate from the customer's internal CA
- Internal NTP and DNS from the customer's network are available
- Updates are delivered via USB or LAN file share as signed bundles
- No telemetry, no external calls — the box is a black box on the LAN

**Internet Enabled (optional)**:
- Customer configures internet access on the box's network interface
- Caddy switches to ACME auto-TLS (Let's Encrypt or customer's ACME server)
- Model registry can pull from HuggingFace Hub in addition to local paths
- System can check for update availability (download still requires admin approval)

### Build Machine (Internet Connected)

```bash
# 1. Clone repo and install deps
git clone vault-ai-backend
cd vault-ai-backend
uv pip compile pyproject.toml -o requirements.txt
pip download -r requirements.txt -d ./wheels

# 2. Download models
./scripts/download_models.sh

# 3. Package everything
./scripts/package_release.sh   # Creates signed tarball with wheels, models, containers
```

### Target Machine (Air-gapped)

```bash
# 1. Transfer tarball (USB/NAS)
# 2. Run installer
./scripts/setup_airgap.sh

# 3. Start services
docker compose up -d

# 4. Verify
./scripts/health_check.sh
```

---

## Risk Register

| Risk | Impact | Mitigation | Stage |
|------|--------|------------|-------|
| vLLM tensor parallelism issues on 5090 | High | Test on hardware ASAP; fallback to replica mode | 1 |
| Training OOMs inference | High | Strict GPU isolation; never share GPUs between modes | 5 |
| Model loading deadlock | Medium | Implement timeout + forced unload | 3 |
| SQLite write contention | Medium | SQLAlchemy ORM enables PostgreSQL swap | 3 |
| Redis queue backup | Medium | Dead letter queue + alerts | 5 |
| Air-gap missing deps | High | Test on clean Ubuntu VM before each release | 2 |
| ClamAV signature staleness | Medium | Bundle fresh sigs with updates, dashboard warning | 3 |
| Model licensing blocks distribution | High | Legal review of every pre-loaded model's license | 1 |

---

## Testing Strategy

### Unit Tests
- Service layer logic (mocked dependencies)
- Pydantic schema validation
- Auth middleware (valid/invalid/expired keys)
- Error response format consistency

### Integration Tests
- API endpoint flows end-to-end (with mock vLLM)
- Database operations (SQLAlchemy async sessions)
- Streaming SSE response parsing

### Load Tests (Stage 3+ — Locust)
```python
class ChatUser(HttpUser):
    @task
    def chat(self):
        self.client.post("/v1/chat/completions", json={
            "model": "qwen2.5-32b-awq",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        }, headers={"Authorization": "Bearer vault_sk_..."})
```

### Smoke Tests
- Model loads and responds
- Chat returns streaming SSE
- Health endpoint reports all services up
- API key auth rejects bad keys

---

## Success Criteria

### Rev 1 (Stage 2)
- [ ] 3 API endpoints operational with auth
- [ ] Streaming chat completions working end-to-end
- [ ] Request logging captures every request
- [ ] Mock vLLM enables development without GPU
- [ ] Chat UI connected and functional
- [ ] Pilot customer using the system

### Stage 3
- [ ] Full 59-endpoint API operational
- [ ] Quarantine scanning all file uploads (Stages 1–3)
- [ ] Update mechanism tested end-to-end
- [ ] Audit log captures every request with user attribution
- [ ] Backup/restore verified on replacement hardware

### Stage 5
- [ ] Training job completes end-to-end via chat UI
- [ ] LoRA adapters load/unload without restarting inference
- [ ] GPU scheduler prevents training from starving inference
- [ ] Eval produces meaningful base-vs-fine-tuned comparison

---

## Resolved Questions

1. **Model storage**: Local NVMe by default (`/data/models/`). NFS mount is optional for multi-node scaling over customer LAN — configure via `VAULT_MODEL_PATH` env var.
2. **Backup strategy**: Rev 1: manual export via CLI. Stage 3: automated daily SQLite/PostgreSQL dumps + training checkpoint retention (last 3 per job). Optional LAN backup target (NAS/file share) configurable in settings.
3. **Update mechanism**: Rev 1: manual SSH updates for pilot units. Stage 3: Signed update bundles (GPG + SHA-256) delivered via USB. Admin applies via management UI.
4. **Database choice**: Rev 1 uses SQLite via SQLAlchemy async ORM. Migration to PostgreSQL happens at Stage 3 — ORM makes this a config change, not a rewrite.
5. **Auth model**: Rev 1 uses API keys (simple, no token refresh, works air-gapped). JWT + LDAP integration added at Stage 4 for organizations with directory services.
6. **Model format**: safetensors only. Pickle-based formats rejected — pickle can execute arbitrary code during deserialization.
