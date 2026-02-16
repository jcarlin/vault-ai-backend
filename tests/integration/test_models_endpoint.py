import pytest


class TestModelsEndpoint:
    async def test_returns_model_list(self, auth_client):
        """GET /v1/models returns models from live backend."""
        response = await auth_client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1

    async def test_401_without_auth(self, anon_client):
        """GET /v1/models without auth returns 401."""
        response = await anon_client.get("/v1/models")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"

    async def test_response_format_matches_schema(self, auth_client):
        """GET /v1/models response matches ModelListResponse schema."""
        response = await auth_client.get("/v1/models")
        data = response.json()

        assert "object" in data
        assert "data" in data
        assert isinstance(data["data"], list)

        for model in data["data"]:
            assert "id" in model
            assert "name" in model

    async def test_auto_discovers_metadata_from_backend(self, auth_client):
        """GET /v1/models returns rich metadata auto-discovered from Ollama-style backend."""
        response = await auth_client.get("/v1/models")
        data = response.json()
        assert len(data["data"]) >= 1

        model = data["data"][0]
        # These come from the Ollama /api/tags mock
        assert model["parameters"] == "32.5B"
        assert model["quantization"] == "Q4_0"
        assert model["family"] == "qwen2"
        assert model["size_bytes"] == 21474836480
        assert model["vram_required_gb"] is not None

    async def test_enriches_with_context_window(self, auth_client):
        """GET /v1/models includes context_window from /api/show enrichment."""
        response = await auth_client.get("/v1/models")
        data = response.json()
        model = data["data"][0]
        assert model["context_window"] == 32768

    async def test_merges_manifest_description(self, auth_client):
        """GET /v1/models merges manifest descriptions when model ID matches."""
        response = await auth_client.get("/v1/models")
        data = response.json()
        model = data["data"][0]
        # The manifest has a description for qwen2.5-32b-awq, and the mock
        # returns qwen2.5-32b-awq:latest — merge should match via prefix strip
        assert model["description"] is not None
