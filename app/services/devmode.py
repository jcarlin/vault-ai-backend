"""DevMode service — state management, session tracking, model inspection."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import select

import app.core.database as db_module
from app.core.database import SystemConfig
from app.config import settings
from app.schemas.devmode import (
    DevModeStatusResponse,
    ModelArchitecture,
    ModelFiles,
    ModelFileInfo,
    ModelInspection,
    QuantizationInfo,
    SessionInfo,
)

logger = structlog.get_logger()

# In-memory session registry (terminal, python, jupyter)
_active_sessions: dict[str, SessionInfo] = {}


async def _get_config(key: str, default: str = "") -> str:
    """Read a value from SystemConfig."""
    async with db_module.async_session() as session:
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == key)
        )
        row = result.scalar_one_or_none()
        return row.value if row else default


async def _set_config(key: str, value: str) -> None:
    """Write a value to SystemConfig (upsert)."""
    async with db_module.async_session() as session:
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = value
        else:
            session.add(SystemConfig(key=key, value=value))
        await session.commit()


async def is_devmode_enabled() -> bool:
    val = await _get_config("devmode.enabled", "false")
    return val == "true"


async def enable_devmode(gpu_allocation: list[int] | None = None) -> DevModeStatusResponse:
    await _set_config("devmode.enabled", "true")
    if gpu_allocation is not None:
        await _set_config("devmode.gpu_allocation", json.dumps(gpu_allocation))
    logger.info("devmode_enabled", gpu_allocation=gpu_allocation)
    return await get_devmode_status()


async def disable_devmode() -> DevModeStatusResponse:
    await _set_config("devmode.enabled", "false")
    # Terminate all active sessions
    terminated = list(_active_sessions.keys())
    _active_sessions.clear()
    logger.info("devmode_disabled", terminated_sessions=terminated)
    return await get_devmode_status()


async def get_devmode_status() -> DevModeStatusResponse:
    enabled = await is_devmode_enabled()
    gpu_raw = await _get_config("devmode.gpu_allocation", "[]")
    try:
        gpu_allocation = json.loads(gpu_raw)
    except (json.JSONDecodeError, ValueError):
        gpu_allocation = []
    return DevModeStatusResponse(
        enabled=enabled,
        gpu_allocation=gpu_allocation,
        active_sessions=list(_active_sessions.values()),
    )


def register_session(session_type: str) -> str:
    """Register a new session and return its ID."""
    session_id = uuid.uuid4().hex[:16]
    info = SessionInfo(
        session_id=session_id,
        session_type=session_type,
        created_at=datetime.now(timezone.utc).isoformat() + "Z",
    )
    _active_sessions[session_id] = info
    logger.info("devmode_session_started", session_id=session_id, session_type=session_type)
    return session_id


def unregister_session(session_id: str) -> bool:
    """Remove a session. Returns True if it existed."""
    removed = _active_sessions.pop(session_id, None)
    if removed:
        logger.info("devmode_session_ended", session_id=session_id)
    return removed is not None


def get_session(session_id: str) -> SessionInfo | None:
    return _active_sessions.get(session_id)


# ── Model Inspector ──────────────────────────────────────────────────────────


async def inspect_model(model_id: str) -> ModelInspection:
    """Inspect a model's config, architecture, quantization, and files on disk."""
    # Look up model path from manifest
    manifest_path = Path(settings.vault_models_manifest)
    model_path: Path | None = None

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for m in manifest.get("models", []):
            if m.get("id") == model_id:
                model_path = Path(m["path"])
                break

    if model_path is None:
        # Fallback: try models_dir / model_id
        model_path = Path(settings.vault_models_dir) / model_id

    if not model_path.exists():
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Model directory not found: {model_path}")

    # Read config.json (HuggingFace standard)
    config_file = model_path / "config.json"
    raw_config: dict = {}
    if config_file.exists():
        raw_config = json.loads(config_file.read_text())

    # Architecture
    architecture = ModelArchitecture(
        model_type=raw_config.get("model_type"),
        num_hidden_layers=raw_config.get("num_hidden_layers"),
        hidden_size=raw_config.get("hidden_size"),
        num_attention_heads=raw_config.get("num_attention_heads"),
        num_key_value_heads=raw_config.get("num_key_value_heads"),
        intermediate_size=raw_config.get("intermediate_size"),
        vocab_size=raw_config.get("vocab_size"),
        max_position_embeddings=raw_config.get("max_position_embeddings"),
        rope_theta=raw_config.get("rope_theta"),
        torch_dtype=raw_config.get("torch_dtype"),
    )

    # Quantization
    quantization: QuantizationInfo | None = None
    quant_config_file = model_path / "quantize_config.json"
    if quant_config_file.exists():
        qc = json.loads(quant_config_file.read_text())
        quantization = QuantizationInfo(
            method=qc.get("quant_method"),
            bits=qc.get("bits"),
            group_size=qc.get("group_size"),
            zero_point=qc.get("zero_point"),
            version=qc.get("version"),
        )
    elif raw_config.get("quantization_config"):
        qc = raw_config["quantization_config"]
        quantization = QuantizationInfo(
            method=qc.get("quant_method"),
            bits=qc.get("bits"),
            group_size=qc.get("group_size"),
            zero_point=qc.get("zero_point"),
            version=qc.get("version"),
        )

    # Files
    file_list: list[ModelFileInfo] = []
    safetensors_count = 0
    total_size = 0
    has_tokenizer = False

    for f in sorted(model_path.iterdir()):
        if f.is_file():
            size = f.stat().st_size
            file_list.append(ModelFileInfo(name=f.name, size_bytes=size))
            total_size += size
            if f.suffix == ".safetensors":
                safetensors_count += 1
            if f.name in ("tokenizer.json", "tokenizer_config.json", "tokenizer.model"):
                has_tokenizer = True

    files = ModelFiles(
        total_size_bytes=total_size,
        safetensors_count=safetensors_count,
        has_tokenizer=has_tokenizer,
        files=file_list,
    )

    return ModelInspection(
        model_id=model_id,
        path=str(model_path),
        architecture=architecture,
        quantization=quantization,
        files=files,
        raw_config=raw_config,
    )
