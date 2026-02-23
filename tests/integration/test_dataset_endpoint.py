"""Integration tests for dataset endpoints (Epic 22)."""

import json

import pytest
import pytest_asyncio


# ── POST /vault/datasets — register manually ─────────────────────────────


@pytest.mark.asyncio
async def test_create_dataset(auth_client, tmp_path):
    ds_file = tmp_path / "my_data.jsonl"
    ds_file.write_text('{"text": "hello"}\n')

    resp = await auth_client.post("/vault/datasets", json={
        "name": "Manual Dataset",
        "source_path": str(ds_file),
        "dataset_type": "training",
        "format": "jsonl",
        "tags": ["test", "v1"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Manual Dataset"
    assert data["dataset_type"] == "training"
    assert data["format"] == "jsonl"
    assert data["status"] == "registered"
    assert data["tags"] == ["test", "v1"]
    assert data["file_size_bytes"] > 0


@pytest.mark.asyncio
async def test_create_dataset_minimal(auth_client):
    resp = await auth_client.post("/vault/datasets", json={
        "name": "Minimal",
        "source_path": "/some/path.csv",
        "format": "csv",
    })
    assert resp.status_code == 201
    assert resp.json()["dataset_type"] == "other"


# ── GET /vault/datasets ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_datasets_empty(auth_client):
    resp = await auth_client.get("/vault/datasets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_datasets(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    await auth_client.post("/vault/datasets", json={
        "name": "DS-A", "source_path": str(ds_file), "format": "jsonl", "dataset_type": "training",
    })
    await auth_client.post("/vault/datasets", json={
        "name": "DS-B", "source_path": str(ds_file), "format": "csv", "dataset_type": "eval",
    })

    resp = await auth_client.get("/vault/datasets")
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_datasets_filter_type(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    await auth_client.post("/vault/datasets", json={
        "name": "Train", "source_path": str(ds_file), "format": "jsonl", "dataset_type": "training",
    })
    await auth_client.post("/vault/datasets", json={
        "name": "Eval", "source_path": str(ds_file), "format": "jsonl", "dataset_type": "eval",
    })

    resp = await auth_client.get("/vault/datasets", params={"type": "training"})
    assert resp.json()["total"] == 1
    assert resp.json()["datasets"][0]["name"] == "Train"


@pytest.mark.asyncio
async def test_list_datasets_search(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    await auth_client.post("/vault/datasets", json={
        "name": "Alpha Data", "source_path": str(ds_file), "format": "jsonl",
    })
    await auth_client.post("/vault/datasets", json={
        "name": "Beta Data", "source_path": str(ds_file), "format": "jsonl",
    })

    resp = await auth_client.get("/vault/datasets", params={"search": "Alpha"})
    assert resp.json()["total"] == 1


# ── GET /vault/datasets/{id} ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_dataset(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    create_resp = await auth_client.post("/vault/datasets", json={
        "name": "Fetch Me", "source_path": str(ds_file), "format": "jsonl",
    })
    ds_id = create_resp.json()["id"]

    resp = await auth_client.get(f"/vault/datasets/{ds_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Fetch Me"


@pytest.mark.asyncio
async def test_get_dataset_not_found(auth_client):
    resp = await auth_client.get("/vault/datasets/nonexistent-uuid")
    assert resp.status_code == 404


# ── PUT /vault/datasets/{id} ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_dataset(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    create_resp = await auth_client.post("/vault/datasets", json={
        "name": "Original", "source_path": str(ds_file), "format": "jsonl",
    })
    ds_id = create_resp.json()["id"]

    resp = await auth_client.put(f"/vault/datasets/{ds_id}", json={
        "name": "Updated Name",
        "tags": ["new-tag"],
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"
    assert resp.json()["tags"] == ["new-tag"]


# ── DELETE /vault/datasets/{id} ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_dataset(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    create_resp = await auth_client.post("/vault/datasets", json={
        "name": "To Delete", "source_path": str(ds_file), "format": "jsonl",
    })
    ds_id = create_resp.json()["id"]

    resp = await auth_client.delete(f"/vault/datasets/{ds_id}")
    assert resp.status_code == 204

    # Verify gone
    get_resp = await auth_client.get(f"/vault/datasets/{ds_id}")
    assert get_resp.status_code == 404


# ── POST /vault/datasets/upload ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_dataset(auth_client, tmp_path):
    from app.config import settings
    original_dir = settings.vault_datasets_dir
    settings.vault_datasets_dir = str(tmp_path / "uploads")

    content = b'{"text": "uploaded line 1"}\n{"text": "uploaded line 2"}\n'
    resp = await auth_client.post(
        "/vault/datasets/upload",
        files={"file": ("test_data.jsonl", content, "application/octet-stream")},
        data={"name": "Uploaded DS", "dataset_type": "training"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Uploaded DS"
    assert data["format"] == "jsonl"
    assert data["file_size_bytes"] == len(content)

    settings.vault_datasets_dir = original_dir


# ── POST /vault/datasets/{id}/validate ───────────────────────────────────


@pytest.mark.asyncio
async def test_validate_dataset(auth_client, tmp_path):
    ds_file = tmp_path / "valid.jsonl"
    ds_file.write_text('{"prompt": "hi", "response": "hello"}\n{"prompt": "a", "response": "b"}\n')

    create_resp = await auth_client.post("/vault/datasets", json={
        "name": "Validate Me", "source_path": str(ds_file), "format": "jsonl",
    })
    ds_id = create_resp.json()["id"]

    resp = await auth_client.post(f"/vault/datasets/{ds_id}/validate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["record_count"] == 2
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_validate_invalid_dataset(auth_client, tmp_path):
    ds_file = tmp_path / "bad.jsonl"
    ds_file.write_text('{"good": true}\nnot json at all\n')

    create_resp = await auth_client.post("/vault/datasets", json={
        "name": "Bad Data", "source_path": str(ds_file), "format": "jsonl",
    })
    ds_id = create_resp.json()["id"]

    resp = await auth_client.post(f"/vault/datasets/{ds_id}/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert len(resp.json()["errors"]) > 0


# ── GET /vault/datasets/{id}/preview ─────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_dataset(auth_client, tmp_path):
    ds_file = tmp_path / "preview.jsonl"
    lines = [json.dumps({"idx": i}) for i in range(20)]
    ds_file.write_text("\n".join(lines) + "\n")

    create_resp = await auth_client.post("/vault/datasets", json={
        "name": "Preview Me", "source_path": str(ds_file), "format": "jsonl",
    })
    ds_id = create_resp.json()["id"]

    resp = await auth_client.get(f"/vault/datasets/{ds_id}/preview", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["preview_records"]) == 5
    assert data["preview_records"][0]["idx"] == 0


# ── GET /vault/datasets/by-type/{type} ───────────────────────────────────


@pytest.mark.asyncio
async def test_list_by_type(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    await auth_client.post("/vault/datasets", json={
        "name": "Train1", "source_path": str(ds_file), "format": "jsonl", "dataset_type": "training",
    })
    await auth_client.post("/vault/datasets", json={
        "name": "Eval1", "source_path": str(ds_file), "format": "jsonl", "dataset_type": "eval",
    })

    resp = await auth_client.get("/vault/datasets/by-type/training")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["datasets"][0]["dataset_type"] == "training"


# ── GET /vault/datasets/stats ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dataset_stats(auth_client, tmp_path):
    ds_file = tmp_path / "d.jsonl"
    ds_file.write_text('{"a":1}\n')

    await auth_client.post("/vault/datasets", json={
        "name": "A", "source_path": str(ds_file), "format": "jsonl", "dataset_type": "training",
    })
    await auth_client.post("/vault/datasets", json={
        "name": "B", "source_path": str(ds_file), "format": "csv", "dataset_type": "eval",
    })

    resp = await auth_client.get("/vault/datasets/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_datasets"] == 2
    assert data["by_type"]["training"] == 1
    assert data["by_type"]["eval"] == 1
