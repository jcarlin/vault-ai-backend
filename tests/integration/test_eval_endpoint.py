"""Integration tests for eval endpoints."""

import json
from pathlib import Path

import pytest
import pytest_asyncio

from app.schemas.eval import EvalConfig


@pytest.mark.asyncio
async def test_create_eval_job(auth_client, tmp_path):
    """POST /vault/eval/jobs — creates a job."""
    # Create a test dataset
    dataset = tmp_path / "test.jsonl"
    dataset.write_text(
        '{"prompt": "Capital of France?", "expected": "Paris"}\n'
        '{"prompt": "2+2?", "expected": "4"}\n'
    )

    resp = await auth_client.post("/vault/eval/jobs", json={
        "name": "Test Eval",
        "model_id": "qwen2.5-32b-awq",
        "dataset_id": str(dataset),
        "config": {"metrics": ["accuracy"], "batch_size": 5},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Eval"
    assert data["status"] == "queued"
    assert data["model_id"] == "qwen2.5-32b-awq"
    assert data["config"]["metrics"] == ["accuracy"]


@pytest.mark.asyncio
async def test_create_eval_job_dataset_not_found(auth_client):
    """POST /vault/eval/jobs — 404 if dataset not found."""
    resp = await auth_client.post("/vault/eval/jobs", json={
        "name": "Bad",
        "model_id": "qwen2.5",
        "dataset_id": "nonexistent-dataset-xyz",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_eval_jobs_empty(auth_client):
    """GET /vault/eval/jobs — empty list initially."""
    resp = await auth_client.get("/vault/eval/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["jobs"] == []


@pytest.mark.asyncio
async def test_list_eval_jobs(auth_client, tmp_path):
    """GET /vault/eval/jobs — returns created jobs."""
    dataset = tmp_path / "test.jsonl"
    dataset.write_text('{"prompt": "Q?", "expected": "A"}\n')

    await auth_client.post("/vault/eval/jobs", json={
        "name": "Job 1", "model_id": "m1", "dataset_id": str(dataset),
    })
    await auth_client.post("/vault/eval/jobs", json={
        "name": "Job 2", "model_id": "m2", "dataset_id": str(dataset),
    })

    resp = await auth_client.get("/vault/eval/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_list_eval_jobs_filter_model(auth_client, tmp_path):
    """GET /vault/eval/jobs?model_id=m1 — filters by model."""
    dataset = tmp_path / "test.jsonl"
    dataset.write_text('{"prompt": "Q?", "expected": "A"}\n')

    await auth_client.post("/vault/eval/jobs", json={
        "name": "A", "model_id": "model-a", "dataset_id": str(dataset),
    })
    await auth_client.post("/vault/eval/jobs", json={
        "name": "B", "model_id": "model-b", "dataset_id": str(dataset),
    })

    resp = await auth_client.get("/vault/eval/jobs", params={"model_id": "model-a"})
    data = resp.json()
    assert data["total"] == 1
    assert data["jobs"][0]["model_id"] == "model-a"


@pytest.mark.asyncio
async def test_list_eval_jobs_filter_status(auth_client, tmp_path):
    """GET /vault/eval/jobs?status=queued — filters by status."""
    dataset = tmp_path / "test.jsonl"
    dataset.write_text('{"prompt": "Q?", "expected": "A"}\n')

    await auth_client.post("/vault/eval/jobs", json={
        "name": "A", "model_id": "m", "dataset_id": str(dataset),
    })

    resp = await auth_client.get("/vault/eval/jobs", params={"status": "queued"})
    assert resp.json()["total"] == 1

    resp = await auth_client.get("/vault/eval/jobs", params={"status": "completed"})
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_get_eval_job(auth_client, tmp_path):
    """GET /vault/eval/jobs/{id} — returns job details."""
    dataset = tmp_path / "test.jsonl"
    dataset.write_text('{"prompt": "Q?", "expected": "A"}\n')

    create_resp = await auth_client.post("/vault/eval/jobs", json={
        "name": "Test", "model_id": "m", "dataset_id": str(dataset),
    })
    job_id = create_resp.json()["id"]

    resp = await auth_client.get(f"/vault/eval/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


@pytest.mark.asyncio
async def test_get_eval_job_not_found(auth_client):
    """GET /vault/eval/jobs/{id} — 404 for nonexistent."""
    resp = await auth_client.get("/vault/eval/jobs/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_eval_job(auth_client, tmp_path):
    """POST /vault/eval/jobs/{id}/cancel — cancels queued job."""
    dataset = tmp_path / "test.jsonl"
    dataset.write_text('{"prompt": "Q?", "expected": "A"}\n')

    create_resp = await auth_client.post("/vault/eval/jobs", json={
        "name": "Test", "model_id": "m", "dataset_id": str(dataset),
    })
    job_id = create_resp.json()["id"]

    resp = await auth_client.post(f"/vault/eval/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_eval_job_already_cancelled(auth_client, tmp_path):
    """POST /vault/eval/jobs/{id}/cancel — 409 if already cancelled."""
    dataset = tmp_path / "test.jsonl"
    dataset.write_text('{"prompt": "Q?", "expected": "A"}\n')

    create_resp = await auth_client.post("/vault/eval/jobs", json={
        "name": "Test", "model_id": "m", "dataset_id": str(dataset),
    })
    job_id = create_resp.json()["id"]

    await auth_client.post(f"/vault/eval/jobs/{job_id}/cancel")
    resp = await auth_client.post(f"/vault/eval/jobs/{job_id}/cancel")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_eval_job(auth_client, tmp_path):
    """DELETE /vault/eval/jobs/{id} — deletes job."""
    dataset = tmp_path / "test.jsonl"
    dataset.write_text('{"prompt": "Q?", "expected": "A"}\n')

    create_resp = await auth_client.post("/vault/eval/jobs", json={
        "name": "Test", "model_id": "m", "dataset_id": str(dataset),
    })
    job_id = create_resp.json()["id"]

    resp = await auth_client.delete(f"/vault/eval/jobs/{job_id}")
    assert resp.status_code == 204

    resp = await auth_client.get(f"/vault/eval/jobs/{job_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_eval_job_not_found(auth_client):
    """DELETE /vault/eval/jobs/{id} — 404 for nonexistent."""
    resp = await auth_client.delete("/vault/eval/jobs/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_compare_eval_jobs_not_enough(auth_client):
    """GET /vault/eval/compare — 400 if fewer than 2 job IDs."""
    resp = await auth_client.get("/vault/eval/compare", params={"job_ids": "single-id"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_compare_eval_jobs_not_found(auth_client):
    """GET /vault/eval/compare — 404 if jobs don't exist."""
    resp = await auth_client.get("/vault/eval/compare", params={"job_ids": "a,b"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_quick_eval(auth_client):
    """POST /vault/eval/quick — runs synchronous quick eval."""
    resp = await auth_client.post("/vault/eval/quick", json={
        "model_id": "qwen2.5-32b-awq",
        "test_cases": [
            {"prompt": "Capital of France?", "expected": "Paris"},
            {"prompt": "2+2?", "expected": "4"},
        ],
        "metrics": ["accuracy"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "aggregate_scores" in data
    assert "duration_ms" in data
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_quick_eval_no_cases(auth_client):
    """POST /vault/eval/quick — 422 when test_cases field is missing."""
    resp = await auth_client.post("/vault/eval/quick", json={
        "model_id": "qwen2.5",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_eval_datasets(auth_client):
    """GET /vault/eval/datasets — returns dataset list."""
    resp = await auth_client.get("/vault/eval/datasets")
    assert resp.status_code == 200
    data = resp.json()
    assert "datasets" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_eval_job_unauthenticated(anon_client):
    """All eval endpoints require authentication."""
    resp = await anon_client.get("/vault/eval/jobs")
    assert resp.status_code == 401

    resp = await anon_client.post("/vault/eval/jobs", json={"name": "X", "model_id": "m", "dataset_id": "d"})
    assert resp.status_code == 401

    resp = await anon_client.post("/vault/eval/quick", json={"model_id": "m", "test_cases": []})
    assert resp.status_code == 401
