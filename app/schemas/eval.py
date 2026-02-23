from pydantic import BaseModel, Field


# ── Config ────────────────────────────────────────────────────────────────────


class EvalConfig(BaseModel):
    metrics: list[str] = ["accuracy"]
    num_samples: int | None = None  # None = entire dataset
    few_shot: int = 0
    batch_size: int = 10
    max_tokens: int = 256
    temperature: float = 0.0


# ── Job CRUD ──────────────────────────────────────────────────────────────────


class EvalJobCreate(BaseModel):
    name: str
    model_id: str
    adapter_id: str | None = None
    dataset_id: str
    config: EvalConfig = EvalConfig()


class EvalMetricResult(BaseModel):
    metric: str
    score: float
    ci_lower: float | None = None
    ci_upper: float | None = None


class EvalExampleResult(BaseModel):
    index: int
    prompt: str
    expected: str | None = None
    generated: str
    scores: dict[str, float] = {}
    correct: bool | None = None


class EvalResults(BaseModel):
    metrics: list[EvalMetricResult] = []
    per_example: list[EvalExampleResult] = []
    summary: str | None = None


class EvalJobResponse(BaseModel):
    id: str
    name: str
    status: str
    progress: float
    model_id: str
    adapter_id: str | None = None
    dataset_id: str
    dataset_type: str = "builtin"
    config: EvalConfig
    results: EvalResults | None = None
    error: str | None = None
    total_examples: int = 0
    examples_completed: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str


class EvalJobList(BaseModel):
    jobs: list[EvalJobResponse]
    total: int


# ── Compare ───────────────────────────────────────────────────────────────────


class EvalCompareEntry(BaseModel):
    job_id: str
    model_id: str
    adapter_id: str | None = None
    label: str
    metrics: list[EvalMetricResult] = []


class EvalCompareResponse(BaseModel):
    dataset_id: str
    models: list[EvalCompareEntry]


# ── Quick Eval ────────────────────────────────────────────────────────────────


class QuickEvalCase(BaseModel):
    prompt: str
    expected: str | None = None
    system_prompt: str | None = None


class QuickEvalRequest(BaseModel):
    model_id: str
    adapter_id: str | None = None
    test_cases: list[QuickEvalCase] = Field(..., max_length=50)
    metrics: list[str] = ["accuracy"]
    max_tokens: int = 256
    temperature: float = 0.0


class QuickEvalCaseResult(BaseModel):
    index: int
    prompt: str
    expected: str | None = None
    generated: str
    scores: dict[str, float] = {}
    correct: bool | None = None


class QuickEvalResponse(BaseModel):
    results: list[QuickEvalCaseResult]
    aggregate_scores: dict[str, float] = {}
    duration_ms: int = 0


# ── Datasets ──────────────────────────────────────────────────────────────────


class EvalDatasetInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    record_count: int = 0
    categories: list[str] = []
    metrics: list[str] = []
    type: str = "builtin"


class EvalDatasetList(BaseModel):
    datasets: list[EvalDatasetInfo]
    total: int
