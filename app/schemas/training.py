from pydantic import BaseModel


class TrainingConfig(BaseModel):
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 0.0001
    warmup_steps: int = 100
    weight_decay: float = 0.01
    optimizer: str = "adamw"
    scheduler: str = "cosine"


class TrainingMetrics(BaseModel):
    loss: float | None = None
    accuracy: float | None = None
    learning_rate: float | None = None
    epochs_completed: int = 0
    total_epochs: int = 0
    tokens_processed: int = 0
    estimated_time_remaining: str | None = None


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
    error: str | None = None
    started_at: str | None
    completed_at: str | None
    created_at: str


class TrainingJobList(BaseModel):
    jobs: list[TrainingJobResponse]
    total: int
