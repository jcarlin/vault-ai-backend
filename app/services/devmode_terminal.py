"""Terminal session management — bash PTY via devmode_pty."""

import structlog

from app.config import settings
from app.services.devmode_pty import PTYSession

logger = structlog.get_logger()

# Active terminal sessions: session_id → PTYSession
_terminal_sessions: dict[str, PTYSession] = {}


async def create_terminal_session(session_id: str) -> PTYSession:
    """Create and start a new bash terminal session."""
    shell = settings.vault_devmode_terminal_shell
    env = {
        "TERM": "xterm-256color",
        "VAULT_SESSION": session_id,
    }
    session = PTYSession(session_id=session_id, cmd=[shell, "--login"], env=env)
    await session.start()
    _terminal_sessions[session_id] = session
    return session


async def destroy_terminal_session(session_id: str) -> None:
    """Terminate and remove a terminal session."""
    session = _terminal_sessions.pop(session_id, None)
    if session:
        await session.terminate()


def get_terminal_session(session_id: str) -> PTYSession | None:
    """Get an active terminal session by ID."""
    return _terminal_sessions.get(session_id)
