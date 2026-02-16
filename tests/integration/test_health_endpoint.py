import pytest


class TestHealthEndpoint:
    async def test_returns_health_without_auth(self, anon_client):
        """GET /vault/health does NOT require auth."""
        response = await anon_client.get("/vault/health")
        assert response.status_code == 200

    async def test_vllm_status_connected(self, anon_client):
        """GET /vault/health shows vLLM as connected when fake vLLM is running."""
        response = await anon_client.get("/vault/health")
        data = response.json()
        assert data["vllm_status"] == "connected"
        assert data["status"] == "ok"

    async def test_gpu_info_empty_on_dev(self, anon_client):
        """GET /vault/health returns empty GPU list on dev machines (no NVIDIA driver)."""
        response = await anon_client.get("/vault/health")
        data = response.json()
        # On a dev machine without NVIDIA GPUs, this should be 0
        assert data["gpu_count"] == 0
        assert data["gpus"] == []

    async def test_health_response_fields(self, anon_client):
        """GET /vault/health includes all required fields."""
        response = await anon_client.get("/vault/health")
        data = response.json()
        assert "status" in data
        assert "vllm_status" in data
        assert "gpu_count" in data
        assert "gpus" in data
        assert "uptime_seconds" in data
        assert "version" in data
        assert data["version"] == "0.1.0"
