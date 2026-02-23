"""Unit tests for PTY session management (terminal + python)."""

import asyncio
import os
import platform

import pytest

from app.services.devmode_pty import PTYSession


@pytest.fixture
def echo_session():
    """Create a PTY session that runs a simple echo command."""
    return PTYSession(
        session_id="test-session-001",
        cmd=["/bin/sh", "-c", "echo hello && sleep 0.5"],
        env={"TERM": "xterm-256color"},
    )


class TestPTYSession:
    async def test_start_creates_pid(self, echo_session):
        await echo_session.start()
        try:
            assert echo_session.pid is not None
            assert echo_session.pid > 0
            assert echo_session.master_fd is not None
        finally:
            await echo_session.terminate()

    async def test_read_returns_output(self, echo_session):
        await echo_session.start()
        try:
            # Give it a moment to produce output
            await asyncio.sleep(0.3)
            output = b""
            for _ in range(20):
                data = await echo_session.read()
                if data:
                    output += data
                if b"hello" in output:
                    break
                await asyncio.sleep(0.05)
            assert b"hello" in output
        finally:
            await echo_session.terminate()

    async def test_write_sends_input(self):
        session = PTYSession(
            session_id="test-write",
            cmd=["/bin/sh"],
            env={"TERM": "xterm-256color"},
        )
        await session.start()
        try:
            # Send a command
            session.write(b"echo testwrite123\n")
            await asyncio.sleep(0.5)
            output = b""
            for _ in range(20):
                data = await session.read()
                if data:
                    output += data
                if b"testwrite123" in output:
                    break
                await asyncio.sleep(0.05)
            assert b"testwrite123" in output
        finally:
            await session.terminate()

    async def test_resize_does_not_crash(self, echo_session):
        await echo_session.start()
        try:
            # Resize should not raise
            echo_session.resize(120, 40)
        finally:
            await echo_session.terminate()

    async def test_terminate_cleans_up(self, echo_session):
        await echo_session.start()
        pid = echo_session.pid
        await echo_session.terminate()

        assert echo_session._closed is True
        assert echo_session.master_fd is None

    async def test_double_terminate_safe(self, echo_session):
        await echo_session.start()
        await echo_session.terminate()
        # Second terminate should not raise
        await echo_session.terminate()

    async def test_is_alive_after_start(self):
        session = PTYSession(
            session_id="alive-test",
            cmd=["/bin/sh", "-c", "sleep 5"],
            env={},
        )
        await session.start()
        try:
            assert session.is_alive is True
        finally:
            await session.terminate()

    async def test_is_alive_after_terminate(self, echo_session):
        await echo_session.start()
        await echo_session.terminate()
        assert echo_session.is_alive is False


class TestTerminalSessionManager:
    async def test_create_and_destroy(self):
        from app.services.devmode_terminal import (
            create_terminal_session,
            destroy_terminal_session,
            get_terminal_session,
        )

        session = await create_terminal_session("mgr-test-001")
        try:
            assert session is not None
            assert get_terminal_session("mgr-test-001") is session
        finally:
            await destroy_terminal_session("mgr-test-001")
            assert get_terminal_session("mgr-test-001") is None

    async def test_destroy_nonexistent(self):
        from app.services.devmode_terminal import destroy_terminal_session

        # Should not raise
        await destroy_terminal_session("nonexistent-session")


class TestPythonSessionManager:
    async def test_create_and_destroy(self):
        from app.services.devmode_python import (
            create_python_session,
            destroy_python_session,
            get_python_session,
        )

        session = await create_python_session("py-test-001")
        try:
            assert session is not None
            assert get_python_session("py-test-001") is session
        finally:
            await destroy_python_session("py-test-001")
            assert get_python_session("py-test-001") is None
