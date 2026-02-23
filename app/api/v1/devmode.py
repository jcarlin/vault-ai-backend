"""Developer mode endpoints — enable/disable, model inspection, sessions."""

from fastapi import APIRouter, Depends

from app.core.exceptions import NotFoundError, VaultError
from app.dependencies import require_admin
from app.schemas.devmode import (
    DevModeEnableRequest,
    DevModeStatusResponse,
    JupyterResponse,
    ModelInspection,
    SessionResponse,
)
from app.services import devmode as devmode_service

router = APIRouter(dependencies=[Depends(require_admin)])


# ── DevMode State ────────────────────────────────────────────────────────────


@router.post("/vault/admin/devmode/enable")
async def enable_devmode(body: DevModeEnableRequest | None = None) -> DevModeStatusResponse:
    """Enable developer mode. Optionally specify GPU allocation."""
    gpu = body.gpu_allocation if body else None
    return await devmode_service.enable_devmode(gpu)


@router.post("/vault/admin/devmode/disable")
async def disable_devmode() -> DevModeStatusResponse:
    """Disable developer mode and terminate active sessions."""
    return await devmode_service.disable_devmode()


@router.get("/vault/admin/devmode/status")
async def devmode_status() -> DevModeStatusResponse:
    """Get developer mode status and active sessions."""
    return await devmode_service.get_devmode_status()


# ── Model Inspector ──────────────────────────────────────────────────────────


@router.get("/vault/admin/devmode/model/{model_id}/inspect")
async def inspect_model(model_id: str) -> ModelInspection:
    """Inspect model architecture, quantization, and files on disk."""
    return await devmode_service.inspect_model(model_id)


# ── Terminal Sessions ────────────────────────────────────────────────────────


@router.post("/vault/admin/devmode/terminal", status_code=201)
async def start_terminal() -> SessionResponse:
    """Start a new terminal PTY session."""
    from app.services.devmode_terminal import create_terminal_session

    session_id = devmode_service.register_session("terminal")
    try:
        await create_terminal_session(session_id)
    except Exception as exc:
        devmode_service.unregister_session(session_id)
        raise VaultError(
            code="terminal_start_failed",
            message=f"Failed to start terminal: {exc}",
            status=500,
        ) from exc
    return SessionResponse(
        session_id=session_id,
        ws_url=f"/ws/terminal?session={session_id}",
    )


@router.delete("/vault/admin/devmode/terminal")
async def stop_terminal(session_id: str) -> dict:
    """Terminate a terminal session."""
    from app.services.devmode_terminal import destroy_terminal_session

    await destroy_terminal_session(session_id)
    devmode_service.unregister_session(session_id)
    return {"status": "terminated", "session_id": session_id}


# ── Python Console Sessions ─────────────────────────────────────────────────


@router.post("/vault/admin/devmode/python", status_code=201)
async def start_python() -> SessionResponse:
    """Start a new Python/IPython PTY session."""
    from app.services.devmode_python import create_python_session

    session_id = devmode_service.register_session("python")
    try:
        await create_python_session(session_id)
    except Exception as exc:
        devmode_service.unregister_session(session_id)
        raise VaultError(
            code="python_start_failed",
            message=f"Failed to start Python console: {exc}",
            status=500,
        ) from exc
    return SessionResponse(
        session_id=session_id,
        ws_url=f"/ws/python?session={session_id}",
    )


@router.delete("/vault/admin/devmode/python")
async def stop_python(session_id: str) -> dict:
    """Terminate a Python console session."""
    from app.services.devmode_python import destroy_python_session

    await destroy_python_session(session_id)
    devmode_service.unregister_session(session_id)
    return {"status": "terminated", "session_id": session_id}


# ── Jupyter Notebooks ────────────────────────────────────────────────────────


@router.post("/vault/admin/devmode/jupyter", status_code=201)
async def start_jupyter() -> JupyterResponse:
    """Launch Jupyter notebook container."""
    from app.services.devmode_jupyter import JupyterManager

    manager = JupyterManager()
    result = await manager.launch()

    if result.get("status") == "running":
        devmode_service.register_session("jupyter")

    return JupyterResponse(
        status=result["status"],
        url=result.get("url"),
        token=result.get("token"),
        message=result.get("message"),
    )


@router.delete("/vault/admin/devmode/jupyter")
async def stop_jupyter() -> JupyterResponse:
    """Stop and remove Jupyter container."""
    from app.services.devmode_jupyter import JupyterManager

    manager = JupyterManager()
    result = await manager.stop()

    # Unregister all jupyter sessions
    for sid, info in list(devmode_service._active_sessions.items()):
        if info.session_type == "jupyter":
            devmode_service.unregister_session(sid)

    return JupyterResponse(
        status=result["status"],
        message=result.get("message"),
    )
