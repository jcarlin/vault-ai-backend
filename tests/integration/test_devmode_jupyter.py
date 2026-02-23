"""Integration tests for Jupyter notebook devmode endpoints."""

import pytest
from unittest.mock import MagicMock, patch


class TestJupyterEndpoints:
    async def test_start_requires_admin(self, anon_client):
        response = await anon_client.post("/vault/admin/devmode/jupyter")
        assert response.status_code == 401

    async def test_stop_requires_admin(self, anon_client):
        response = await anon_client.request("DELETE", "/vault/admin/devmode/jupyter")
        assert response.status_code == 401

    async def test_start_returns_error_without_docker(self, auth_client):
        """Without Docker installed, launch should return an error status."""
        response = await auth_client.post("/vault/admin/devmode/jupyter")
        assert response.status_code == 201
        data = response.json()
        # Docker isn't available in test env, so expect error
        assert data["status"] == "error"
        assert data["message"] is not None

    async def test_stop_without_running(self, auth_client):
        """Stopping when nothing is running should return stopped or error (no Docker)."""
        response = await auth_client.request("DELETE", "/vault/admin/devmode/jupyter")
        assert response.status_code == 200
        data = response.json()
        # Without Docker, we get "error"; with Docker but no container, "stopped"
        assert data["status"] in ("stopped", "error")


class TestJupyterManagerMocked:
    async def test_launch_with_mock_docker(self, auth_client):
        """Test launch with a mocked Docker client."""
        mock_client = MagicMock()

        # Container doesn't exist yet (raises NotFound)
        mock_client.containers.get.side_effect = Exception("No such container")

        # Mock container run
        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_client.info.return_value = {"Runtimes": {}}

        with patch("app.services.devmode_jupyter.JupyterManager._get_docker_client", return_value=mock_client):
            response = await auth_client.post("/vault/admin/devmode/jupyter")
            assert response.status_code == 201
            data = response.json()
            assert data["status"] == "running"
            assert data["url"] is not None
            assert data["token"] is not None

    async def test_stop_with_mock_docker(self, auth_client):
        """Test stop with a mocked Docker client."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("app.services.devmode_jupyter.JupyterManager._get_docker_client", return_value=mock_client):
            response = await auth_client.request("DELETE", "/vault/admin/devmode/jupyter")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "stopped"
            mock_container.stop.assert_called_once()
            mock_container.remove.assert_called_once()

    async def test_launch_already_running(self, auth_client):
        """If container is already running, return its info."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.attrs = {
            "Config": {"Env": ["JUPYTER_TOKEN=existingtoken123"]}
        }
        mock_client.containers.get.return_value = mock_container

        with patch("app.services.devmode_jupyter.JupyterManager._get_docker_client", return_value=mock_client):
            response = await auth_client.post("/vault/admin/devmode/jupyter")
            assert response.status_code == 201
            data = response.json()
            assert data["status"] == "running"
            assert data["token"] == "existingtoken123"
