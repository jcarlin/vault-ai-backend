import asyncio
import json
import shutil
from pathlib import Path

import structlog
import yaml

from app.config import settings
from app.core.exceptions import NotFoundError, VaultError

logger = structlog.get_logger()

DANGEROUS_EXTENSIONS = {".pkl", ".pickle", ".bin"}


class ModelManager:
    def __init__(self):
        self._models_dir = Path(settings.vault_models_dir)
        self._manifest_path = Path(settings.vault_models_manifest)
        self._gpu_config_path = Path(settings.vault_gpu_config_path)
        self._container_name = settings.vault_vllm_container_name

    def _load_manifest(self) -> list[dict]:
        if not self._manifest_path.exists():
            return []
        with open(self._manifest_path) as f:
            data = json.load(f)
        return data.get("models", [])

    def _save_manifest(self, models: list[dict]) -> None:
        with open(self._manifest_path, "w") as f:
            json.dump({"models": models}, f, indent=2)

    def _load_gpu_config(self) -> dict:
        if not self._gpu_config_path.exists():
            return {"strategy": "replica", "models": []}
        with open(self._gpu_config_path) as f:
            return yaml.safe_load(f) or {"strategy": "replica", "models": []}

    def _save_gpu_config(self, config: dict) -> None:
        self._gpu_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._gpu_config_path, "w") as f:
            yaml.safe_dump(config, f)

    @staticmethod
    def _normalize_id(model_id: str) -> str:
        """Strip :latest suffix for comparison (backend may append it)."""
        return model_id.rsplit(":", 1)[0] if ":" in model_id else model_id

    async def _get_running_ids(self, backend) -> set[str]:
        """Get normalized IDs of running models from the backend."""
        try:
            live_models = await backend.list_models()
            return {self._normalize_id(m.id) for m in live_models if m.status == "running"}
        except Exception:
            return set()

    async def list_models(self, backend=None) -> list[dict]:
        manifest = self._load_manifest()
        loaded_ids: set[str] = set()
        if backend:
            loaded_ids = await self._get_running_ids(backend)

        result = []
        for m in manifest:
            info = dict(m)
            info["status"] = "loaded" if m["id"] in loaded_ids else "available"
            result.append(info)
        return result

    async def get_model(self, model_id: str, backend=None) -> dict:
        manifest = self._load_manifest()
        model = next((m for m in manifest if m["id"] == model_id), None)
        if model is None:
            raise NotFoundError(f"Model '{model_id}' not found in manifest.")

        info = dict(model)
        # Check if loaded
        if backend:
            loaded_ids = await self._get_running_ids(backend)
            info["status"] = "loaded" if model_id in loaded_ids else "available"

        # Load GPU config for allocation info
        gpu_config = self._load_gpu_config()
        for cfg_model in gpu_config.get("models", []):
            if cfg_model.get("id") == model_id:
                info["gpu_index"] = cfg_model.get("gpus", [0])[0] if cfg_model.get("gpus") else None
                break

        return info

    async def load_model(self, model_id: str, gpu_index: int = 0, docker_client=None) -> dict:
        # Validate model exists in manifest
        manifest = self._load_manifest()
        model = next((m for m in manifest if m["id"] == model_id), None)
        if model is None:
            raise NotFoundError(f"Model '{model_id}' not found in manifest.")

        # Update gpu-config.yaml
        gpu_config = self._load_gpu_config()
        # Remove any existing entry for this model
        gpu_config["models"] = [m for m in gpu_config.get("models", []) if m.get("id") != model_id]
        gpu_config["models"].append({"id": model_id, "gpus": [gpu_index], "mode": "replica"})
        self._save_gpu_config(gpu_config)

        # Restart vLLM container (via Docker SDK or subprocess)
        if docker_client:
            await asyncio.to_thread(self._restart_vllm_container, docker_client)

        return {"status": "loading", "message": f"Model {model_id} loading on GPU {gpu_index}", "model_id": model_id}

    async def unload_model(self, model_id: str, docker_client=None) -> dict:
        gpu_config = self._load_gpu_config()
        gpu_config["models"] = [m for m in gpu_config.get("models", []) if m.get("id") != model_id]
        self._save_gpu_config(gpu_config)

        if docker_client:
            await asyncio.to_thread(self._restart_vllm_container, docker_client)

        return {"status": "unloaded", "message": f"Model {model_id} unloaded", "model_id": model_id}

    async def get_active_models(self, backend=None) -> dict:
        gpu_config = self._load_gpu_config()
        manifest = self._load_manifest()
        manifest_by_id = {m["id"]: m for m in manifest}

        models = []
        gpu_allocation = []
        for cfg in gpu_config.get("models", []):
            model_id = cfg.get("id")
            info = manifest_by_id.get(model_id, {"id": model_id, "name": model_id})
            info = dict(info)
            info["status"] = "loaded"
            info["gpu_index"] = cfg.get("gpus", [0])[0] if cfg.get("gpus") else None
            models.append(info)
            gpu_allocation.append({"model_id": model_id, "gpus": cfg.get("gpus", [])})

        return {"models": models, "gpu_allocation": gpu_allocation}

    async def import_model(self, source_path: str, model_id: str | None = None, quarantine_pipeline=None) -> dict:
        source = Path(source_path)
        if not source.exists():
            raise NotFoundError(f"Source path '{source_path}' not found.")
        if not source.is_dir():
            raise VaultError(code="validation_error", message="Source path must be a directory.", status=400)

        # Security: reject pickle files
        for f in source.rglob("*"):
            if f.suffix.lower() in DANGEROUS_EXTENSIONS:
                raise VaultError(
                    code="security_violation",
                    message=f"Dangerous file detected: {f.name}. Pickle/binary files are not allowed.",
                    status=400,
                )

        # Validate model directory (must have config.json or safetensors)
        has_config = (source / "config.json").exists()
        has_safetensors = any(source.glob("*.safetensors"))
        if not has_config and not has_safetensors:
            raise VaultError(
                code="validation_error",
                message="Invalid model directory: must contain config.json or .safetensors files.",
                status=400,
            )

        # Determine model_id
        if not model_id:
            model_id = source.name

        dest = self._models_dir / model_id
        if dest.exists():
            raise VaultError(code="conflict", message=f"Model '{model_id}' already exists.", status=409)

        # Route through quarantine pipeline if available
        if quarantine_pipeline:
            files_to_scan = []
            for f in source.rglob("*"):
                if f.is_file():
                    files_to_scan.append((str(f.relative_to(source)), f.read_bytes()))
            if files_to_scan:
                job_id = await quarantine_pipeline.submit_scan(
                    files_to_scan, source_type="model_import", submitted_by="model_manager"
                )
                logger.info("model_import_quarantine_submitted", model_id=model_id, job_id=job_id)

        # Copy (potentially multi-GB, so use thread)
        await asyncio.to_thread(shutil.copytree, source, dest)

        # Add to manifest
        manifest = self._load_manifest()
        manifest.append({"id": model_id, "name": model_id, "path": str(dest)})
        self._save_manifest(manifest)

        return {"status": "imported", "message": f"Model {model_id} imported successfully", "model_id": model_id}

    async def delete_model(self, model_id: str, backend=None) -> dict:
        # Check if loaded
        if backend:
            loaded_ids = await self._get_running_ids(backend)
            if model_id in loaded_ids:
                raise VaultError(
                    code="conflict",
                    message=f"Model '{model_id}' is currently loaded. Unload it first.",
                    status=409,
                )

        # Remove from manifest
        manifest = self._load_manifest()
        model = next((m for m in manifest if m["id"] == model_id), None)
        if model is None:
            raise NotFoundError(f"Model '{model_id}' not found in manifest.")

        manifest = [m for m in manifest if m["id"] != model_id]
        self._save_manifest(manifest)

        # Remove from disk if path exists
        model_path = model.get("path")
        if model_path:
            path = Path(model_path)
            if path.exists():
                await asyncio.to_thread(shutil.rmtree, path)

        return {"status": "deleted", "message": f"Model {model_id} deleted"}

    def _restart_vllm_container(self, docker_client) -> None:
        """Restart the vLLM Docker container (sync, called via to_thread)."""
        try:
            container = docker_client.containers.get(self._container_name)
            container.restart(timeout=30)
        except Exception as e:
            logger.warning("vllm_container_restart_failed", error=str(e))
