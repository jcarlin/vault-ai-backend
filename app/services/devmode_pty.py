"""Shared PTY-WebSocket bridge utility for terminal and Python console sessions."""

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios

import structlog

logger = structlog.get_logger()


class PTYSession:
    """Manages a pseudo-terminal subprocess with async read/write."""

    def __init__(self, session_id: str, cmd: list[str], env: dict[str, str] | None = None):
        self.session_id = session_id
        self.cmd = cmd
        self.env = env or {}
        self.master_fd: int | None = None
        self.pid: int | None = None
        self._closed = False

    async def start(self) -> None:
        """Fork a PTY and exec the command."""
        env = {**os.environ, **self.env}
        # Remove VIRTUAL_ENV from parent if we're setting our own
        if "VIRTUAL_ENV" in self.env and "VIRTUAL_ENV" in env:
            pass  # already overwritten

        self.pid, self.master_fd = pty.openpty()
        # pty.openpty returns (master_fd, slave_fd) — but we need fork
        # Actually use pty.fork() for proper subprocess
        os.close(self.pid)
        os.close(self.master_fd)

        self.pid, self.master_fd = pty.fork()

        if self.pid == 0:
            # Child process
            os.environ.clear()
            os.environ.update(env)
            os.execvp(self.cmd[0], self.cmd)
        else:
            # Parent: set master_fd to non-blocking
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            logger.info(
                "pty_session_started",
                session_id=self.session_id,
                pid=self.pid,
                cmd=self.cmd[0],
            )

    async def read(self, size: int = 4096) -> bytes:
        """Read from the PTY master fd (async via executor)."""
        if self._closed or self.master_fd is None:
            return b""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._blocking_read, size)
        except OSError:
            return b""

    def _blocking_read(self, size: int) -> bytes:
        """Blocking read with short timeout for use in executor."""
        import select as sel

        if self.master_fd is None:
            return b""
        try:
            ready, _, _ = sel.select([self.master_fd], [], [], 0.1)
            if ready:
                return os.read(self.master_fd, size)
        except (OSError, ValueError):
            pass
        return b""

    def write(self, data: bytes) -> None:
        """Write to the PTY master fd."""
        if self._closed or self.master_fd is None:
            return
        try:
            os.write(self.master_fd, data)
        except OSError as exc:
            logger.debug("pty_write_failed", session_id=self.session_id, error=str(exc))

    def resize(self, cols: int, rows: int) -> None:
        """Send TIOCSWINSZ to resize the PTY."""
        if self._closed or self.master_fd is None:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def terminate(self) -> None:
        """Kill the subprocess and close file descriptors."""
        if self._closed:
            return
        self._closed = True

        if self.pid and self.pid > 0:
            try:
                os.kill(self.pid, signal.SIGTERM)
                # Give it a moment to exit
                await asyncio.sleep(0.2)
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(self.pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            except ProcessLookupError:
                pass

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        logger.info("pty_session_terminated", session_id=self.session_id)

    @property
    def is_alive(self) -> bool:
        if self._closed or self.pid is None:
            return False
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            return pid == 0  # 0 means still running
        except ChildProcessError:
            return False
