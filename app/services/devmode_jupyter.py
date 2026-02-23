"""Jupyter notebook container lifecycle management via Docker SDK."""

import secrets

import structlog

from app.config import settings

logger = structlog.get_logger()

CONTAINER_NAME = "vault-jupyter"


class JupyterManager:
    """Manages a Jupyter notebook Docker container."""

    def __init__(self):
        self._client = None

    def _get_docker_client(self):
        """Lazy import and create Docker client."""
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
            except ImportError:
                logger.warning("docker_sdk_not_installed")
                raise RuntimeError("Docker SDK not installed. Install with: pip install docker")
            except Exception as exc:
                logger.warning("docker_connect_failed", error=str(exc))
                raise RuntimeError(f"Cannot connect to Docker: {exc}")
        return self._client

    async def launch(self) -> dict:
        """Launch the Jupyter container. Returns status + URL + token."""
        try:
            client = self._get_docker_client()
        except RuntimeError as exc:
            return {"status": "error", "message": str(exc)}

        # Check if already running
        try:
            container = client.containers.get(CONTAINER_NAME)
            if container.status == "running":
                # Extract token from environment
                env_list = container.attrs.get("Config", {}).get("Env", [])
                token = ""
                for e in env_list:
                    if e.startswith("JUPYTER_TOKEN="):
                        token = e.split("=", 1)[1]
                        break
                port = settings.vault_devmode_jupyter_port
                return {
                    "status": "running",
                    "url": f"http://localhost:{port}",
                    "token": token,
                    "message": "Jupyter is already running",
                }
            else:
                container.remove(force=True)
        except Exception:
            pass  # Container doesn't exist

        # Generate token
        token = secrets.token_hex(24)
        port = settings.vault_devmode_jupyter_port

        try:
            container = client.containers.run(
                image=settings.vault_devmode_jupyter_image,
                name=CONTAINER_NAME,
                detach=True,
                ports={"8888/tcp": port},
                environment={
                    "JUPYTER_TOKEN": token,
                    "JUPYTER_ENABLE_LAB": "yes",
                },
                volumes={
                    settings.vault_models_dir: {
                        "bind": "/home/jovyan/models",
                        "mode": "ro",
                    },
                },
                # GPU passthrough (nvidia runtime)
                device_requests=[
                    {
                        "Driver": "nvidia",
                        "Count": -1,
                        "Capabilities": [["gpu"]],
                    }
                ] if self._gpu_available(client) else [],
                restart_policy={"Name": "unless-stopped"},
            )
            logger.info(
                "jupyter_container_launched",
                container_id=container.short_id,
                port=port,
            )
            return {
                "status": "running",
                "url": f"http://localhost:{port}",
                "token": token,
                "message": "Jupyter launched successfully",
            }
        except Exception as exc:
            logger.error("jupyter_launch_failed", error=str(exc))
            return {"status": "error", "message": f"Failed to launch Jupyter: {exc}"}

    async def stop(self) -> dict:
        """Stop and remove the Jupyter container."""
        try:
            client = self._get_docker_client()
        except RuntimeError as exc:
            return {"status": "error", "message": str(exc)}

        try:
            container = client.containers.get(CONTAINER_NAME)
            container.stop(timeout=10)
            container.remove()
            logger.info("jupyter_container_stopped")
            return {"status": "stopped", "message": "Jupyter stopped and removed"}
        except Exception as exc:
            logger.warning("jupyter_stop_failed", error=str(exc))
            return {"status": "stopped", "message": f"Container may not have been running: {exc}"}

    async def status(self) -> dict:
        """Check if the Jupyter container is running."""
        try:
            client = self._get_docker_client()
            container = client.containers.get(CONTAINER_NAME)
            return {"status": container.status}
        except Exception:
            return {"status": "stopped"}

    def _gpu_available(self, client) -> bool:
        """Check if NVIDIA runtime is available."""
        try:
            info = client.info()
            runtimes = info.get("Runtimes", {})
            return "nvidia" in runtimes
        except Exception:
            return False
