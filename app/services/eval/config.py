"""Eval run configuration — serialized to JSON and passed to the worker subprocess."""

from pydantic import BaseModel


class EvalRunConfig(BaseModel):
    """Full configuration passed to the eval worker subprocess."""

    job_id: str
    model_id: str
    adapter_id: str | None = None
    dataset_path: str
    dataset_type: str = "builtin"
    status_dir: str  # directory where worker writes status.json

    # API gateway connection (worker calls through gateway, not direct GPU)
    api_base_url: str = "http://localhost:8000"
    api_key: str = ""

    # Eval parameters
    metrics: list[str] = ["accuracy"]
    num_samples: int | None = None
    few_shot: int = 0
    batch_size: int = 10
    max_tokens: int = 256
    temperature: float = 0.0
