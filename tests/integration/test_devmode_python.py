"""Integration tests for Python console devmode endpoints."""

import pytest


class TestPythonConsoleEndpoints:
    async def test_start_requires_admin(self, anon_client):
        response = await anon_client.post("/vault/admin/devmode/python")
        assert response.status_code == 401

    async def test_start_returns_session(self, auth_client):
        response = await auth_client.post("/vault/admin/devmode/python")
        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert "/ws/python" in data["ws_url"]

        # Clean up
        await auth_client.request(
            "DELETE",
            "/vault/admin/devmode/python",
            params={"session_id": data["session_id"]},
        )

    async def test_stop_returns_terminated(self, auth_client):
        start_resp = await auth_client.post("/vault/admin/devmode/python")
        sid = start_resp.json()["session_id"]

        stop_resp = await auth_client.request(
            "DELETE",
            "/vault/admin/devmode/python",
            params={"session_id": sid},
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "terminated"
