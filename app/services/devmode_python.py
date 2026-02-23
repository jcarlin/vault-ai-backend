"""Python console session management — IPython PTY via devmode_pty."""

import shutil
import sys
from pathlib import Path

import structlog

from app.config import settings
from app.services.devmode_pty import PTYSession

logger = structlog.get_logger()

# Active python sessions: session_id → PTYSession
_python_sessions: dict[str, PTYSession] = {}


def _find_python() -> str:
    """Find the Python executable to use for the console."""
    # Cube: use the PyTorch venv's python
    venv_python = Path(settings.vault_devmode_python_venv) / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    # Dev: use current python's IPython if available
    return sys.executable


def _find_ipython_cmd() -> list[str]:
    """Build the command to launch IPython."""
    python = _find_python()
    # Check if IPython is available
    if shutil.which("ipython"):
        return ["ipython", "--colors=Linux", "--no-banner"]
    # Fall back to python -m IPython
    return [python, "-m", "IPython", "--colors=Linux", "--no-banner"]


async def create_python_session(session_id: str) -> PTYSession:
    """Create and start a new Python/IPython console session."""
    cmd = _find_ipython_cmd()
    env = {
        "TERM": "xterm-256color",
        "VAULT_SESSION": session_id,
        "VAULT_MODELS_DIR": settings.vault_models_dir,
    }
    # If using the PyTorch venv, set VIRTUAL_ENV
    venv_path = Path(settings.vault_devmode_python_venv)
    if venv_path.exists():
        env["VIRTUAL_ENV"] = str(venv_path)
        env["PATH"] = f"{venv_path}/bin:{env.get('PATH', '')}"

    session = PTYSession(session_id=session_id, cmd=cmd, env=env)
    await session.start()
    _python_sessions[session_id] = session
    return session


async def destroy_python_session(session_id: str) -> None:
    """Terminate and remove a Python console session."""
    session = _python_sessions.pop(session_id, None)
    if session:
        await session.terminate()


def get_python_session(session_id: str) -> PTYSession | None:
    """Get an active Python session by ID."""
    return _python_sessions.get(session_id)
