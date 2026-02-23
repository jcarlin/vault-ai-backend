from pydantic import BaseModel


class TrainingConfig(BaseModel):
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 0.0001
    warmup_steps: int = 100
    weight_decay: float = 0.01
    optimizer: str = "adamw"
    scheduler: str = "cosine"


class LoRAConfig(BaseModel):
    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    target_modules: list[str] = ["q_proj", "v_proj"]
    quantization_bits: int | None = None  # None = full precision, 4 = QLoRA, 8 = 8-bit


class TrainingMetrics(BaseModel):
    loss: float | None = None
    accuracy: float | None = None
    learning_rate: float | None = None
    epochs_completed: int = 0
    total_epochs: int = 0
    tokens_processed: int = 0
    estimated_time_remaining: str | None = None
    loss_history: list[dict] | None = None
    steps_completed: int = 0
    total_steps: int = 0


class ResourceAllocation(BaseModel):
    gpu_count: int = 1
    gpu_memory: str = "24GB"
    estimated_time: str | None = None
    actual_time: str | None = None


class TrainingJobCreate(BaseModel):
    name: str
    model: str
    dataset: str
    config: TrainingConfig = TrainingConfig()
    resource_allocation: ResourceAllocation = ResourceAllocation()
    adapter_type: str = "lora"  # "lora" or "qlora"
    lora_config: LoRAConfig = LoRAConfig()


class TrainingJobResponse(BaseModel):
    id: str
    name: str
    status: str
    progress: float
    model: str
    dataset: str
    config: TrainingConfig
    metrics: TrainingMetrics
    resource_allocation: ResourceAllocation
    adapter_type: str = "lora"
    lora_config: LoRAConfig | None = None
    adapter_id: str | None = None
    error: str | None = None
    started_at: str | None
    completed_at: str | None
    created_at: str


class TrainingJobList(BaseModel):
    jobs: list[TrainingJobResponse]
    total: int


# ── Dataset Validation ──────────────────────────────────────────────────────


class DatasetValidationRequest(BaseModel):
    path: str


class DatasetValidationResponse(BaseModel):
    valid: bool
    format: str | None = None
    record_count: int = 0
    findings: list[dict] = []


# ── Adapter Management ──────────────────────────────────────────────────────


class AdapterInfo(BaseModel):
    id: str
    name: str
    base_model: str
    adapter_type: str  # "lora" or "qlora"
    status: str  # "ready", "active", "failed"
    path: str
    training_job_id: str | None = None
    config: dict | None = None
    metrics: dict | None = None
    size_bytes: int = 0
    version: int = 1
    created_at: str
    activated_at: str | None = None


class AdapterList(BaseModel):
    adapters: list[AdapterInfo]
    total: int


# ── GPU Allocation ──────────────────────────────────────────────────────────


class GPUAllocationStatus(BaseModel):
    gpu_index: int
    assigned_to: str | None = None  # "inference" or "training"
    job_id: str | None = None
    memory_used_pct: float = 0.0
