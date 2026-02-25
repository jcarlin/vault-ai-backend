"""Integration tests for data source endpoints (Epic 22)."""

import json

import pytest
import pytest_asyncio


# ── POST /vault/admin/datasources ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_datasource(auth_client, tmp_path):
    resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "Local Data",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Local Data"
    assert data["source_type"] == "local"
    assert data["status"] == "active"
    assert data["config"]["path"] == str(tmp_path)


@pytest.mark.asyncio
async def test_create_datasource_invalid_type(auth_client):
    resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "Bad Type",
        "source_type": "ftp",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_datasource_requires_admin(anon_client):
    resp = await anon_client.post("/vault/admin/datasources", json={
        "name": "Test",
        "source_type": "local",
    })
    assert resp.status_code == 401


# ── GET /vault/admin/datasources ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_datasources_empty(auth_client):
    resp = await auth_client.get("/vault/admin/datasources")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["sources"] == []


@pytest.mark.asyncio
async def test_list_datasources(auth_client, tmp_path):
    await auth_client.post("/vault/admin/datasources", json={
        "name": "Source A",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    await auth_client.post("/vault/admin/datasources", json={
        "name": "Source B",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    resp = await auth_client.get("/vault/admin/datasources")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2


# ── PUT /vault/admin/datasources/{id} ───────────────────────────────────


@pytest.mark.asyncio
async def test_update_datasource(auth_client, tmp_path):
    create_resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "Original",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    source_id = create_resp.json()["id"]

    resp = await auth_client.put(f"/vault/admin/datasources/{source_id}", json={
        "name": "Renamed",
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_update_datasource_not_found(auth_client):
    resp = await auth_client.put("/vault/admin/datasources/nonexistent", json={
        "name": "No Such",
    })
    assert resp.status_code == 404


# ── DELETE /vault/admin/datasources/{id} ─────────────────────────────────


@pytest.mark.asyncio
async def test_delete_datasource(auth_client, tmp_path):
    create_resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "ToDelete",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    source_id = create_resp.json()["id"]

    resp = await auth_client.delete(f"/vault/admin/datasources/{source_id}")
    assert resp.status_code == 204

    # Should be hidden from list (disabled sources are filtered out)
    list_resp = await auth_client.get("/vault/admin/datasources")
    sources = list_resp.json()["sources"]
    matching = [s for s in sources if s["id"] == source_id]
    assert len(matching) == 0


@pytest.mark.asyncio
async def test_delete_datasource_not_found(auth_client):
    resp = await auth_client.delete("/vault/admin/datasources/nonexistent")
    assert resp.status_code == 404


# ── POST /vault/admin/datasources/{id}/test ──────────────────────────────


@pytest.mark.asyncio
async def test_test_datasource_success(auth_client, tmp_path):
    # Create some files in tmp_path
    (tmp_path / "data.jsonl").write_text('{"a": 1}\n')

    create_resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "TestConn",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    source_id = create_resp.json()["id"]

    resp = await auth_client.post(f"/vault/admin/datasources/{source_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["files_found"] >= 1


@pytest.mark.asyncio
async def test_test_datasource_bad_path(auth_client):
    create_resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "BadPath",
        "source_type": "local",
        "config": {"path": "/nonexistent/abc123xyz"},
    })
    source_id = create_resp.json()["id"]

    resp = await auth_client.post(f"/vault/admin/datasources/{source_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False


# ── POST /vault/admin/datasources/{id}/scan ──────────────────────────────


@pytest.mark.asyncio
async def test_scan_datasource(auth_client, tmp_path):
    # Create sample files
    (tmp_path / "train.jsonl").write_text('{"text": "hello"}\n{"text": "world"}\n')
    (tmp_path / "eval.csv").write_text("a,b\n1,2\n")
    (tmp_path / "readme.md").write_text("not a dataset")  # should be ignored

    create_resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "ScanSource",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    source_id = create_resp.json()["id"]

    resp = await auth_client.post(f"/vault/admin/datasources/{source_id}/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["datasets_discovered"] >= 2  # jsonl + csv
    assert data["errors"] == []

    # Verify datasets were created in the registry
    ds_resp = await auth_client.get("/vault/datasets", params={"source_id": source_id})
    assert ds_resp.status_code == 200
    assert ds_resp.json()["total"] >= 2


@pytest.mark.asyncio
async def test_scan_datasource_idempotent(auth_client, tmp_path):
    (tmp_path / "data.jsonl").write_text('{"a": 1}\n')

    create_resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "Idempotent",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    source_id = create_resp.json()["id"]

    # Scan twice
    resp1 = await auth_client.post(f"/vault/admin/datasources/{source_id}/scan")
    resp2 = await auth_client.post(f"/vault/admin/datasources/{source_id}/scan")

    assert resp1.json()["datasets_discovered"] == 1
    assert resp2.json()["datasets_discovered"] == 0
    assert resp2.json()["datasets_updated"] == 1
