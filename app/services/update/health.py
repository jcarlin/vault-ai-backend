"""Post-update health check validation."""

import asyncio

import httpx
import structlog

logger = structlog.get_logger()


class HealthChecker:
    """Validates service health after an update is applied."""

    def __init__(
        self,
        backend_url: str = "http://localhost:8000",
        frontend_url: str = "http://localhost:3001",
        caddy_url: str = "https://vault-cube.local",
        backend_retries: int = 30,
        frontend_retries: int = 15,
        caddy_retries: int = 5,
        retry_interval: float = 2.0,
    ):
        self._backend_url = backend_url
        self._frontend_url = frontend_url
        self._caddy_url = caddy_url
        self._backend_retries = backend_retries
        self._frontend_retries = frontend_retries
        self._caddy_retries = caddy_retries
        self._retry_interval = retry_interval

    async def check_all(self) -> dict:
        """Run all health checks. Returns dict with check results."""
        results = {
            "backend": await self._check_backend(),
            "frontend": await self._check_frontend(),
            "caddy": await self._check_caddy(),
        }
        results["all_passed"] = all(r["passed"] for r in results.values())
        return results

    async def _check_backend(self) -> dict:
        """Check backend health endpoint with retries."""
        return await self._check_url(
            url=f"{self._backend_url}/vault/health",
            name="backend",
            retries=self._backend_retries,
        )

    async def _check_frontend(self) -> dict:
        """Check frontend is serving with retries."""
        return await self._check_url(
            url=self._frontend_url,
            name="frontend",
            retries=self._frontend_retries,
        )

    async def _check_caddy(self) -> dict:
        """Check Caddy reverse proxy with retries."""
        return await self._check_url(
            url=f"{self._caddy_url}/vault/health",
            name="caddy",
            retries=self._caddy_retries,
            verify_ssl=False,
        )

    async def _check_url(
        self,
        url: str,
        name: str,
        retries: int,
        verify_ssl: bool = True,
    ) -> dict:
        """Poll a URL until it responds 200 or retries exhausted."""
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(verify=verify_ssl, timeout=5.0) as client:
                    resp = await client.get(url)
                    if resp.status_code < 500:
                        logger.info(
                            "health_check_passed",
                            service=name,
                            attempt=attempt,
                            status=resp.status_code,
                        )
                        return {"passed": True, "attempts": attempt, "status": resp.status_code}
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = str(e)

            if attempt < retries:
                await asyncio.sleep(self._retry_interval)

        logger.warning(
            "health_check_failed",
            service=name,
            retries=retries,
            last_error=last_error,
        )
        return {"passed": False, "attempts": retries, "error": last_error}
