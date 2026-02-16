import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


@pytest.fixture
def job_payload():
    return {
        "name": "Fine-tune Qwen for legal",
        "model": "qwen2.5-32b-awq",
        "dataset": "legal-corpus-v2",
        "config": {
            "epochs": 5,
            "batch_size": 16,
            "learning_rate": 0.0002,
        },
        "resource_allocation": {
            "gpu_count": 2,
            "gpu_memory": "48GB",
        },
    }


@pytest_asyncio.fixture
async def training_app(app_with_db):
    """App with training router included."""
    from app.api.v1.training import router as training_router

    app_with_db.include_router(training_router, tags=["Training"])
    yield app_with_db


@pytest_asyncio.fixture
async def training_auth_client(training_app, db_engine):
    """Authenticated client with training routes available."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="training-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=training_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def training_anon_client(training_app):
    """Unauthenticated client with training routes available."""
    transport = ASGITransport(app=training_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestTrainingJobsAPI:
    async def test_create_job(self, training_auth_client, job_payload):
        """POST /vault/training/jobs creates a new job in queued status."""
        response = await training_auth_client.post("/vault/training/jobs", json=job_payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Fine-tune Qwen for legal"
        assert data["status"] == "queued"
        assert data["progress"] == 0.0
        assert data["model"] == "qwen2.5-32b-awq"
        assert data["dataset"] == "legal-corpus-v2"
        assert data["config"]["epochs"] == 5
        assert data["resource_allocation"]["gpu_count"] == 2
        assert data["id"]
        assert data["created_at"]

    async def test_list_jobs(self, training_auth_client, job_payload):
        """GET /vault/training/jobs returns created jobs."""
        await training_auth_client.post("/vault/training/jobs", json=job_payload)
        response = await training_auth_client.get("/vault/training/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["jobs"]) >= 1
        assert data["jobs"][0]["name"] == "Fine-tune Qwen for legal"

    async def test_get_job(self, training_auth_client, job_payload):
        """GET /vault/training/jobs/{id} returns job details."""
        create_resp = await training_auth_client.post("/vault/training/jobs", json=job_payload)
        job_id = create_resp.json()["id"]

        response = await training_auth_client.get(f"/vault/training/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["metrics"]["epochs_completed"] == 0

    async def test_get_job_not_found(self, training_auth_client):
        """GET /vault/training/jobs/{id} returns 404 for nonexistent job."""
        response = await training_auth_client.get("/vault/training/jobs/nonexistent-id")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_pause_queued_job_returns_409(self, training_auth_client, job_payload):
        """POST /vault/training/jobs/{id}/pause on a queued job returns 409."""
        create_resp = await training_auth_client.post("/vault/training/jobs", json=job_payload)
        job_id = create_resp.json()["id"]

        response = await training_auth_client.post(f"/vault/training/jobs/{job_id}/pause")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_status_transition"

    async def test_resume_queued_job_returns_409(self, training_auth_client, job_payload):
        """POST /vault/training/jobs/{id}/resume on a queued job returns 409."""
        create_resp = await training_auth_client.post("/vault/training/jobs", json=job_payload)
        job_id = create_resp.json()["id"]

        response = await training_auth_client.post(f"/vault/training/jobs/{job_id}/resume")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_status_transition"

    async def test_cancel_job(self, training_auth_client, job_payload):
        """POST /vault/training/jobs/{id}/cancel cancels a queued job."""
        create_resp = await training_auth_client.post("/vault/training/jobs", json=job_payload)
        job_id = create_resp.json()["id"]

        response = await training_auth_client.post(f"/vault/training/jobs/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    async def test_cancel_already_cancelled_returns_409(self, training_auth_client, job_payload):
        """Cancelling an already cancelled job returns 409."""
        create_resp = await training_auth_client.post("/vault/training/jobs", json=job_payload)
        job_id = create_resp.json()["id"]

        await training_auth_client.post(f"/vault/training/jobs/{job_id}/cancel")
        response = await training_auth_client.post(f"/vault/training/jobs/{job_id}/cancel")
        assert response.status_code == 409

    async def test_delete_job(self, training_auth_client, job_payload):
        """DELETE /vault/training/jobs/{id} removes the job record."""
        create_resp = await training_auth_client.post("/vault/training/jobs", json=job_payload)
        job_id = create_resp.json()["id"]

        response = await training_auth_client.delete(f"/vault/training/jobs/{job_id}")
        assert response.status_code == 204

        # Confirm it's gone
        get_resp = await training_auth_client.get(f"/vault/training/jobs/{job_id}")
        assert get_resp.status_code == 404

    async def test_401_without_auth(self, training_anon_client):
        """Training endpoints require authentication."""
        response = await training_anon_client.get("/vault/training/jobs")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
