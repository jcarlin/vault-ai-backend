import pytest


class TestEmbeddingsEndpoint:
    async def test_single_input(self, auth_client):
        """POST /v1/embeddings with a single string input."""
        response = await auth_client.post(
            "/v1/embeddings",
            json={"model": "nomic-embed-text:latest", "input": "test sentence"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 1
        assert data["data"][0]["object"] == "embedding"
        assert data["data"][0]["index"] == 0
        assert len(data["data"][0]["embedding"]) == 384
        assert data["model"] == "nomic-embed-text:latest"
        assert data["usage"]["prompt_tokens"] == 5

    async def test_list_input(self, auth_client):
        """POST /v1/embeddings with a list of strings."""
        response = await auth_client.post(
            "/v1/embeddings",
            json={
                "model": "nomic-embed-text:latest",
                "input": ["first sentence", "second sentence", "third sentence"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 3
        for i, item in enumerate(data["data"]):
            assert item["index"] == i
            assert item["object"] == "embedding"
            assert len(item["embedding"]) == 384
        assert data["usage"]["prompt_tokens"] == 15

    async def test_unauthenticated(self, anon_client):
        """POST /v1/embeddings without auth returns 401."""
        response = await anon_client.post(
            "/v1/embeddings",
            json={"model": "nomic-embed-text:latest", "input": "test"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
