"""Integration tests for terminal and python devmode endpoints."""

import pytest


class TestTerminalSessionLifecycle:
    async def test_start_terminal_returns_session(self, auth_client):
        response = await auth_client.post("/vault/admin/devmode/terminal")
        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert "ws_url" in data
        assert data["ws_url"].startswith("/ws/terminal")

        # Clean up
        session_id = data["session_id"]
        await auth_client.request(
            "DELETE",
            "/vault/admin/devmode/terminal",
            params={"session_id": session_id},
        )

    async def test_stop_terminal(self, auth_client):
        # Start
        start_resp = await auth_client.post("/vault/admin/devmode/terminal")
        session_id = start_resp.json()["session_id"]

        # Stop
        stop_resp = await auth_client.request(
            "DELETE",
            "/vault/admin/devmode/terminal",
            params={"session_id": session_id},
        )
        assert stop_resp.status_code == 200
        data = stop_resp.json()
        assert data["status"] == "terminated"

    async def test_session_appears_in_status(self, auth_client):
        # Enable devmode
        await auth_client.post("/vault/admin/devmode/enable", json={})

        # Start terminal
        start_resp = await auth_client.post("/vault/admin/devmode/terminal")
        session_id = start_resp.json()["session_id"]

        # Check status
        status_resp = await auth_client.get("/vault/admin/devmode/status")
        sessions = status_resp.json()["active_sessions"]
        session_ids = [s["session_id"] for s in sessions]
        assert session_id in session_ids

        # Clean up
        await auth_client.request(
            "DELETE",
            "/vault/admin/devmode/terminal",
            params={"session_id": session_id},
        )


class TestPythonSessionLifecycle:
    async def test_start_python_returns_session(self, auth_client):
        response = await auth_client.post("/vault/admin/devmode/python")
        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert "ws_url" in data
        assert data["ws_url"].startswith("/ws/python")

        # Clean up
        session_id = data["session_id"]
        await auth_client.request(
            "DELETE",
            "/vault/admin/devmode/python",
            params={"session_id": session_id},
        )

    async def test_stop_python(self, auth_client):
        # Start
        start_resp = await auth_client.post("/vault/admin/devmode/python")
        session_id = start_resp.json()["session_id"]

        # Stop
        stop_resp = await auth_client.request(
            "DELETE",
            "/vault/admin/devmode/python",
            params={"session_id": session_id},
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "terminated"
