"""Unit tests for eval schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.eval import (
    EvalConfig,
    EvalJobCreate,
    EvalJobResponse,
    EvalMetricResult,
    EvalResults,
    QuickEvalCase,
    QuickEvalRequest,
    EvalDatasetInfo,
)


def test_eval_config_defaults():
    config = EvalConfig()
    assert config.metrics == ["accuracy"]
    assert config.num_samples is None
    assert config.few_shot == 0
    assert config.batch_size == 10
    assert config.max_tokens == 256
    assert config.temperature == 0.0


def test_eval_config_custom():
    config = EvalConfig(metrics=["accuracy", "f1"], num_samples=100, few_shot=5, batch_size=20)
    assert config.metrics == ["accuracy", "f1"]
    assert config.num_samples == 100
    assert config.few_shot == 5


def test_eval_job_create_minimal():
    data = EvalJobCreate(name="test", model_id="qwen2.5", dataset_id="mmlu-mini")
    assert data.adapter_id is None
    assert data.config.metrics == ["accuracy"]


def test_eval_job_create_with_adapter():
    data = EvalJobCreate(
        name="test",
        model_id="qwen2.5",
        adapter_id="abc-123",
        dataset_id="mmlu-mini",
        config=EvalConfig(metrics=["f1", "bleu"]),
    )
    assert data.adapter_id == "abc-123"
    assert data.config.metrics == ["f1", "bleu"]


def test_eval_job_response_full():
    resp = EvalJobResponse(
        id="job-1",
        name="Test Eval",
        status="completed",
        progress=100.0,
        model_id="qwen2.5",
        dataset_id="mmlu-mini",
        config=EvalConfig(),
        results=EvalResults(
            metrics=[EvalMetricResult(metric="accuracy", score=0.85, ci_lower=0.80, ci_upper=0.90)],
            summary="accuracy: 85.00%",
        ),
        total_examples=200,
        examples_completed=200,
        created_at="2026-02-23T00:00:00Z",
    )
    assert resp.results.metrics[0].score == 0.85
    assert resp.results.summary == "accuracy: 85.00%"


def test_quick_eval_request_valid():
    req = QuickEvalRequest(
        model_id="qwen2.5",
        test_cases=[QuickEvalCase(prompt="Hello?", expected="Hi")],
    )
    assert len(req.test_cases) == 1


def test_quick_eval_request_max_cases():
    cases = [QuickEvalCase(prompt=f"Q{i}?") for i in range(50)]
    req = QuickEvalRequest(model_id="qwen2.5", test_cases=cases)
    assert len(req.test_cases) == 50


def test_eval_dataset_info():
    ds = EvalDatasetInfo(
        id="mmlu-mini",
        name="MMLU Mini",
        description="200 questions",
        record_count=200,
        categories=["history", "science"],
        metrics=["accuracy"],
    )
    assert ds.type == "builtin"
    assert ds.record_count == 200
