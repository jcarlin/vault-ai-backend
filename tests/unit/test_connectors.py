"""Unit tests for data source connectors (Epic 22)."""

import json
import os

import pytest
import pytest_asyncio

from app.services.dataset.connectors import LocalConnector, get_connector


@pytest.fixture
def local_dir(tmp_path):
    """Create a temporary directory with sample files."""
    # Create sample files
    (tmp_path / "train.jsonl").write_text('{"text": "hello"}\n{"text": "world"}\n')
    (tmp_path / "eval.csv").write_text("prompt,response\nhello,world\nfoo,bar\n")
    (tmp_path / "readme.txt").write_text("This is a readme.\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.jsonl").write_text('{"a": 1}\n')
    return tmp_path


@pytest.fixture
def connector(local_dir):
    return LocalConnector(base_path=str(local_dir))


@pytest.mark.asyncio
async def test_local_test_connection_success(connector):
    success, message = await connector.test_connection()
    assert success is True
    assert "Connected" in message


@pytest.mark.asyncio
async def test_local_test_connection_missing_path():
    conn = LocalConnector(base_path="/nonexistent/path/abc123")
    success, message = await conn.test_connection()
    assert success is False
    assert "does not exist" in message


@pytest.mark.asyncio
async def test_local_list_files_jsonl(connector, local_dir):
    files = await connector.list_files(["*.jsonl"])
    assert len(files) == 2  # train.jsonl + sub/nested.jsonl
    paths = {f["relative_path"] for f in files}
    assert "train.jsonl" in paths
    assert os.path.join("sub", "nested.jsonl") in paths


@pytest.mark.asyncio
async def test_local_list_files_csv(connector):
    files = await connector.list_files(["*.csv"])
    assert len(files) == 1
    assert files[0]["relative_path"] == "eval.csv"


@pytest.mark.asyncio
async def test_local_list_files_multiple_patterns(connector):
    files = await connector.list_files(["*.jsonl", "*.csv", "*.txt"])
    assert len(files) == 4  # train.jsonl, eval.csv, readme.txt, sub/nested.jsonl


@pytest.mark.asyncio
async def test_local_list_files_no_matches(connector):
    files = await connector.list_files(["*.parquet"])
    assert len(files) == 0


@pytest.mark.asyncio
async def test_local_read_file_full(connector, local_dir):
    data = await connector.read_file(str(local_dir / "train.jsonl"))
    assert b'"text": "hello"' in data


@pytest.mark.asyncio
async def test_local_read_file_limited(connector, local_dir):
    data = await connector.read_file(str(local_dir / "train.jsonl"), limit=10)
    assert len(data) == 10


@pytest.mark.asyncio
async def test_local_read_file_relative(connector):
    data = await connector.read_file("train.jsonl")
    assert b"hello" in data


@pytest.mark.asyncio
async def test_local_file_info_exists(connector, local_dir):
    info = await connector.file_info(str(local_dir / "train.jsonl"))
    assert info["exists"] is True
    assert info["size"] > 0


@pytest.mark.asyncio
async def test_local_file_info_missing(connector):
    info = await connector.file_info("nonexistent.jsonl")
    assert info["exists"] is False


@pytest.mark.asyncio
async def test_local_file_info_relative(connector):
    info = await connector.file_info("eval.csv")
    assert info["exists"] is True


# ── get_connector factory ─────────────────────────────────────────────────


class FakeDataSource:
    def __init__(self, source_type, config):
        self.source_type = source_type
        self.config_json = json.dumps(config) if config else None


def test_get_connector_local(tmp_path):
    source = FakeDataSource("local", {"path": str(tmp_path)})
    conn = get_connector(source)
    assert isinstance(conn, LocalConnector)


def test_get_connector_local_missing_path():
    source = FakeDataSource("local", {})
    with pytest.raises(ValueError, match="requires 'path'"):
        get_connector(source)


def test_get_connector_s3_missing_keys():
    source = FakeDataSource("s3", {"endpoint": "http://minio:9000"})
    with pytest.raises(ValueError, match="requires config keys"):
        get_connector(source)


def test_get_connector_unsupported():
    source = FakeDataSource("ftp", {})
    with pytest.raises(ValueError, match="Unsupported"):
        get_connector(source)
