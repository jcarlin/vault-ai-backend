import pytest


class TestModelsEndpoint:
    async def test_returns_model_list(self, auth_client):
        """GET /v1/models returns the model manifest."""
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

        # Each model should have at least id and name
        for model in data["data"]:
            assert "id" in model
            assert "name" in model
