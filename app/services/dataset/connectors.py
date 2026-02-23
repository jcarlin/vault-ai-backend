"""Data source connectors for local, S3, SMB, NFS file access (Epic 22)."""

import asyncio
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

import structlog

from app.core.database import DataSource

logger = structlog.get_logger()


class DataSourceConnector(ABC):
    """Abstract base class for data source connectors."""

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """Test connectivity. Returns (success, message)."""
        ...

    @abstractmethod
    async def list_files(self, patterns: list[str]) -> list[dict]:
        """List files matching patterns. Returns list of {path, size, modified}."""
        ...

    @abstractmethod
    async def read_file(self, path: str, limit: int = -1) -> bytes:
        """Read file contents (up to limit bytes, -1 = all)."""
        ...

    @abstractmethod
    async def file_info(self, path: str) -> dict:
        """Get file metadata: {path, size, modified, exists}."""
        ...


class LocalConnector(DataSourceConnector):
    """Connector for local filesystem paths."""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    async def test_connection(self) -> tuple[bool, str]:
        def _test():
            if not self.base_path.exists():
                return False, f"Path does not exist: {self.base_path}"
            if not self.base_path.is_dir():
                return False, f"Path is not a directory: {self.base_path}"
            if not os.access(self.base_path, os.R_OK):
                return False, f"Path is not readable: {self.base_path}"
            return True, f"Connected to {self.base_path}"

        return await asyncio.to_thread(_test)

    async def list_files(self, patterns: list[str]) -> list[dict]:
        def _list():
            results = []
            seen = set()
            for pattern in patterns:
                for p in self.base_path.rglob(pattern):
                    if p.is_file() and str(p) not in seen:
                        seen.add(str(p))
                        stat = p.stat()
                        results.append({
                            "path": str(p),
                            "relative_path": str(p.relative_to(self.base_path)),
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                        })
            return results

        return await asyncio.to_thread(_list)

    async def read_file(self, path: str, limit: int = -1) -> bytes:
        def _read():
            full_path = Path(path)
            if not full_path.is_absolute():
                full_path = self.base_path / path
            if limit > 0:
                with open(full_path, "rb") as f:
                    return f.read(limit)
            return full_path.read_bytes()

        return await asyncio.to_thread(_read)

    async def file_info(self, path: str) -> dict:
        def _info():
            full_path = Path(path)
            if not full_path.is_absolute():
                full_path = self.base_path / path
            if not full_path.exists():
                return {"path": str(full_path), "exists": False, "size": 0, "modified": None}
            stat = full_path.stat()
            return {
                "path": str(full_path),
                "exists": True,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }

        return await asyncio.to_thread(_info)


class S3Connector(DataSourceConnector):
    """Connector for S3-compatible object storage (requires boto3)."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "us-east-1"):
        self.endpoint = endpoint
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def _get_client(self):
        try:
            import boto3
        except ImportError:
            raise RuntimeError("boto3 is required for S3 data sources. Install it with: pip install boto3")
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )

    async def test_connection(self) -> tuple[bool, str]:
        def _test():
            try:
                client = self._get_client()
                client.head_bucket(Bucket=self.bucket)
                return True, f"Connected to s3://{self.bucket}"
            except RuntimeError as e:
                return False, str(e)
            except Exception as e:
                return False, f"S3 connection failed: {e}"

        return await asyncio.to_thread(_test)

    async def list_files(self, patterns: list[str]) -> list[dict]:
        def _list():
            import fnmatch
            client = self._get_client()
            results = []
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    for pattern in patterns:
                        if fnmatch.fnmatch(key, pattern) or fnmatch.fnmatch(os.path.basename(key), pattern):
                            results.append({
                                "path": f"s3://{self.bucket}/{key}",
                                "relative_path": key,
                                "size": obj["Size"],
                                "modified": obj["LastModified"].timestamp(),
                            })
                            break
            return results

        return await asyncio.to_thread(_list)

    async def read_file(self, path: str, limit: int = -1) -> bytes:
        def _read():
            client = self._get_client()
            key = path.replace(f"s3://{self.bucket}/", "")
            kwargs = {"Bucket": self.bucket, "Key": key}
            if limit > 0:
                kwargs["Range"] = f"bytes=0-{limit - 1}"
            resp = client.get_object(**kwargs)
            return resp["Body"].read()

        return await asyncio.to_thread(_read)

    async def file_info(self, path: str) -> dict:
        def _info():
            client = self._get_client()
            key = path.replace(f"s3://{self.bucket}/", "")
            try:
                resp = client.head_object(Bucket=self.bucket, Key=key)
                return {
                    "path": path,
                    "exists": True,
                    "size": resp["ContentLength"],
                    "modified": resp["LastModified"].timestamp(),
                }
            except Exception:
                return {"path": path, "exists": False, "size": 0, "modified": None}

        return await asyncio.to_thread(_info)


def get_connector(source: DataSource) -> DataSourceConnector:
    """Factory: create the appropriate connector for a DataSource."""
    config = json.loads(source.config_json) if source.config_json else {}

    if source.source_type == "local":
        base_path = config.get("path", "")
        if not base_path:
            raise ValueError("Local data source requires 'path' in config")
        return LocalConnector(base_path=base_path)

    elif source.source_type == "s3":
        required = ["endpoint", "bucket", "access_key", "secret_key"]
        missing = [k for k in required if not config.get(k)]
        if missing:
            raise ValueError(f"S3 data source requires config keys: {', '.join(missing)}")
        return S3Connector(
            endpoint=config["endpoint"],
            bucket=config["bucket"],
            access_key=config["access_key"],
            secret_key=config["secret_key"],
            region=config.get("region", "us-east-1"),
        )

    else:
        raise ValueError(f"Unsupported data source type: {source.source_type}")
