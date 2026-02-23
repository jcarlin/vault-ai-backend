from pydantic import BaseModel


# ── DevMode State ────────────────────────────────────────────────────────────


class DevModeEnableRequest(BaseModel):
    gpu_allocation: list[int] | None = None


class SessionInfo(BaseModel):
    session_id: str
    session_type: str  # "terminal", "python", "jupyter"
    created_at: str


class DevModeStatusResponse(BaseModel):
    enabled: bool
    gpu_allocation: list[int]
    active_sessions: list[SessionInfo]


# ── Terminal / Python Sessions ───────────────────────────────────────────────


class SessionResponse(BaseModel):
    session_id: str
    ws_url: str


# ── Jupyter ──────────────────────────────────────────────────────────────────


class JupyterResponse(BaseModel):
    status: str  # "running", "stopped", "starting", "error"
    url: str | None = None
    token: str | None = None
    message: str | None = None


# ── Model Inspector ──────────────────────────────────────────────────────────


class ModelArchitecture(BaseModel):
    model_type: str | None = None
    num_hidden_layers: int | None = None
    hidden_size: int | None = None
    num_attention_heads: int | None = None
    num_key_value_heads: int | None = None
    intermediate_size: int | None = None
    vocab_size: int | None = None
    max_position_embeddings: int | None = None
    rope_theta: float | None = None
    torch_dtype: str | None = None


class QuantizationInfo(BaseModel):
    method: str | None = None  # "awq", "gptq", etc.
    bits: int | None = None
    group_size: int | None = None
    zero_point: bool | None = None
    version: str | None = None


class ModelFileInfo(BaseModel):
    name: str
    size_bytes: int


class ModelFiles(BaseModel):
    total_size_bytes: int
    safetensors_count: int
    has_tokenizer: bool
    files: list[ModelFileInfo]


class ModelInspection(BaseModel):
    model_id: str
    path: str
    architecture: ModelArchitecture
    quantization: QuantizationInfo | None = None
    files: ModelFiles
    raw_config: dict
