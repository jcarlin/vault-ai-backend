"""End-to-end training lifecycle tests (Epic 16).

Tests the full flow: create job → check GPU allocation → validate dataset
→ adapter management. Uses mocked subprocess (no real GPU training).
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class TestTrainingE2E:
    @pytest.mark.asyncio
    async def test_create_job_with_lora_config(self, auth_client):
        """Job creation should accept LoRA config and adapter_type."""
        resp = await auth_client.post(
            "/vault/training/jobs",
            json={
                "name": "legal-finetune",
                "model": "qwen2.5-32b-awq",
                "dataset": "/opt/vault/data/training/legal.jsonl",
                "adapter_type": "lora",
                "lora_config": {
                    "rank": 32,
                    "alpha": 64,
                    "dropout": 0.1,
                    "target_modules": ["q_proj", "k_proj", "v_proj"],
                },
                "config": {
                    "epochs": 5,
                    "batch_size": 16,
                    "learning_rate": 0.0002,
                },
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["adapter_type"] == "lora"
        assert data["lora_config"]["rank"] == 32
        assert data["config"]["epochs"] == 5

    @pytest.mark.asyncio
    async def test_create_qlora_job(self, auth_client):
        """QLoRA jobs should set quantization bits."""
        resp = await auth_client.post(
            "/vault/training/jobs",
            json={
                "name": "qlora-test",
                "model": "llama-3.3-8b-q4",
                "dataset": "/data/test.jsonl",
                "adapter_type": "qlora",
                "lora_config": {
                    "rank": 16,
                    "alpha": 32,
                    "quantization_bits": 4,
                },
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["adapter_type"] == "qlora"
        assert data["lora_config"]["quantization_bits"] == 4

    @pytest.mark.asyncio
    async def test_job_lifecycle(self, auth_client):
        """Full job lifecycle: create → cancel → delete."""
        # Create
        resp = await auth_client.post(
            "/vault/training/jobs",
            json={
                "name": "lifecycle-test",
                "model": "qwen2.5-32b-awq",
                "dataset": "/data/test.jsonl",
            },
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        assert resp.json()["status"] == "queued"

        # Cancel (from queued)
        resp = await auth_client.post(f"/vault/training/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        # Delete
        resp = await auth_client.delete(f"/vault/training/jobs/{job_id}")
        assert resp.status_code == 204

        # Verify gone
        resp = await auth_client.get(f"/vault/training/jobs/{job_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_gpu_allocation_default(self, auth_client):
        """Default GPU allocation should return at least one GPU."""
        resp = await auth_client.get("/vault/training/gpu-allocation")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["assigned_to"] == "inference"
