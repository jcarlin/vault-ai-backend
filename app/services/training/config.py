"""Training run configuration — serialized to JSON and passed to the worker subprocess."""

from pydantic import BaseModel


class TrainingRunConfig(BaseModel):
    """Full configuration passed to the training worker subprocess."""

    job_id: str
    base_model_path: str
    dataset_path: str
    output_dir: str  # /opt/vault/adapters/{job_id}/
    status_dir: str  # directory where worker writes status.json

    # LoRA/QLoRA parameters
    adapter_type: str = "lora"  # "lora" or "qlora"
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_target_modules: list[str] = ["q_proj", "v_proj"]
    quantization_bits: int | None = None  # None = full, 4 = QLoRA

    # Training hyperparameters
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 0.0001
    warmup_steps: int = 100
    weight_decay: float = 0.01
    optimizer: str = "adamw"
    scheduler: str = "cosine"

    # Hardware
    gpu_index: int = 1
    max_memory_pct: float = 0.9

    # Logging
    log_steps: int = 10  # write status.json every N steps
