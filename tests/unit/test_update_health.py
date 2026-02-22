"""Unit tests for the HealthChecker class (app/services/update/health.py)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.update.health import HealthChecker


class TestCheckUrl:
    @pytest.mark.asyncio
    async def test_health_check_passes_on_200(self):
        """Health check passes when service responds with 200."""
        checker = HealthChecker(
            backend_retries=1,
            frontend_retries=1,
            caddy_retries=1,
            retry_interval=0.01,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.services.update.health.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await checker._check_url(
                url="http://localhost:8000/vault/health",
                name="backend",
                retries=3,
            )

        assert result["passed"] is True
        assert result["attempts"] == 1
        assert result["status"] == 200

    @pytest.mark.asyncio
    async def test_health_check_fails_on_500(self):
        """Health check fails when service returns 500 for all retries."""
        checker = HealthChecker(
            backend_retries=1,
            frontend_retries=1,
            caddy_retries=1,
            retry_interval=0.01,
        )

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("app.services.update.health.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await checker._check_url(
                url="http://localhost:8000/vault/health",
                name="backend",
                retries=2,
            )

        assert result["passed"] is False
        assert result["attempts"] == 2

    @pytest.mark.asyncio
    async def test_health_check_fails_on_connection_error(self):
        """Health check fails on connection error."""
        checker = HealthChecker(
            backend_retries=1,
            frontend_retries=1,
            caddy_retries=1,
            retry_interval=0.01,
        )

        with patch("app.services.update.health.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await checker._check_url(
                url="http://localhost:8000/vault/health",
                name="backend",
                retries=2,
            )

        assert result["passed"] is False
        assert "Connection refused" in result["error"]
        assert result["attempts"] == 2

    @pytest.mark.asyncio
    async def test_health_check_retries_then_succeeds(self):
        """Health check retries on failure and eventually succeeds."""
        checker = HealthChecker(
            backend_retries=1,
            frontend_retries=1,
            caddy_retries=1,
            retry_interval=0.01,
        )

        ok_response = MagicMock()
        ok_response.status_code = 200

        # First call fails, second succeeds
        call_count = 0

        async def mock_get(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection refused")
            return ok_response

        with patch("app.services.update.health.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await checker._check_url(
                url="http://localhost:8000/vault/health",
                name="backend",
                retries=5,
            )

        assert result["passed"] is True
        assert result["attempts"] == 2

    @pytest.mark.asyncio
    async def test_health_check_timeout_handling(self):
        """Health check handles httpx.TimeoutException gracefully."""
        checker = HealthChecker(
            backend_retries=1,
            frontend_retries=1,
            caddy_retries=1,
            retry_interval=0.01,
        )

        with patch("app.services.update.health.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await checker._check_url(
                url="http://localhost:8000/vault/health",
                name="backend",
                retries=2,
            )

        assert result["passed"] is False
        assert result["attempts"] == 2
        assert "timed out" in result["error"].lower()


class TestCheckAll:
    @pytest.mark.asyncio
    async def test_check_all_aggregates_results(self):
        """check_all() returns results from all three checks with all_passed flag."""
        checker = HealthChecker(
            backend_retries=1,
            frontend_retries=1,
            caddy_retries=1,
            retry_interval=0.01,
        )

        ok_response = MagicMock()
        ok_response.status_code = 200

        with patch("app.services.update.health.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=ok_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await checker.check_all()

        assert "backend" in results
        assert "frontend" in results
        assert "caddy" in results
        assert results["all_passed"] is True
        assert results["backend"]["passed"] is True
        assert results["frontend"]["passed"] is True
        assert results["caddy"]["passed"] is True
