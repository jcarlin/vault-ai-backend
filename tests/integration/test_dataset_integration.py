"""Integration tests for dataset registry integrations with eval/training (Epic 22)."""

import json

import pytest
import pytest_asyncio


@pytest.mark.asyncio
async def test_eval_resolve_dataset_from_registry(auth_client, tmp_path):
    """Eval should resolve dataset UUID from the registry."""
    ds_file = tmp_path / "eval_data.jsonl"
    ds_file.write_text(
        '{"prompt": "What is 2+2?", "expected": "4"}\n'
        '{"prompt": "Capital of France?", "expected": "Paris"}\n'
    )

    # Register dataset
    create_resp = await auth_client.post("/vault/datasets", json={
        "name": "Eval Registry",
        "source_path": str(ds_file),
        "format": "jsonl",
        "dataset_type": "eval",
    })
    assert create_resp.status_code == 201
    ds_id = create_resp.json()["id"]

    # Create eval job referencing the registered dataset UUID
    eval_resp = await auth_client.post("/vault/eval/jobs", json={
        "name": "Registry Eval Test",
        "model_id": "qwen2.5-32b-awq",
        "dataset_id": ds_id,
    })
    assert eval_resp.status_code == 201
    assert eval_resp.json()["dataset_id"] == ds_id


@pytest.mark.asyncio
async def test_eval_datasets_include_registry(auth_client, tmp_path):
    """GET /vault/eval/datasets should include eval-type datasets from registry."""
    ds_file = tmp_path / "eval_data.jsonl"
    ds_file.write_text('{"prompt": "test"}\n')

    # Register an eval dataset
    await auth_client.post("/vault/datasets", json={
        "name": "Registry Eval DS",
        "source_path": str(ds_file),
        "format": "jsonl",
        "dataset_type": "eval",
    })

    resp = await auth_client.get("/vault/eval/datasets")
    assert resp.status_code == 200
    data = resp.json()

    # Should find our registry dataset
    names = [d["name"] for d in data["datasets"]]
    assert "Registry Eval DS" in names


@pytest.mark.asyncio
async def test_dataset_scan_and_list_flow(auth_client, tmp_path):
    """End-to-end: create source, scan, list discovered datasets."""
    # Create sample data files
    (tmp_path / "training_data.jsonl").write_text('{"text": "sample"}\n{"text": "data"}\n')
    (tmp_path / "eval_data.csv").write_text("prompt,response\nhello,world\n")

    # Create data source
    create_resp = await auth_client.post("/vault/admin/datasources", json={
        "name": "Test Source",
        "source_type": "local",
        "config": {"path": str(tmp_path)},
    })
    assert create_resp.status_code == 201
    source_id = create_resp.json()["id"]

    # Test connectivity
    test_resp = await auth_client.post(f"/vault/admin/datasources/{source_id}/test")
    assert test_resp.json()["success"] is True
    assert test_resp.json()["files_found"] >= 2

    # Scan for datasets
    scan_resp = await auth_client.post(f"/vault/admin/datasources/{source_id}/scan")
    assert scan_resp.status_code == 200
    assert scan_resp.json()["datasets_discovered"] >= 2

    # List discovered datasets
    list_resp = await auth_client.get("/vault/datasets", params={"source_id": source_id})
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 2

    # Verify datasets have correct properties
    datasets = list_resp.json()["datasets"]
    names = {d["name"] for d in datasets}
    assert "training_data" in names or any("training" in n.lower() for n in names)


@pytest.mark.asyncio
async def test_upload_validate_preview_flow(auth_client, tmp_path):
    """End-to-end: upload dataset, validate it, preview records."""
    from app.config import settings
    original_dir = settings.vault_datasets_dir
    settings.vault_datasets_dir = str(tmp_path / "uploads")

    content = b'{"prompt": "hello", "response": "world"}\n{"prompt": "foo", "response": "bar"}\n'

    # Upload
    upload_resp = await auth_client.post(
        "/vault/datasets/upload",
        files={"file": ("test.jsonl", content, "application/octet-stream")},
        data={"name": "Upload Flow Test", "dataset_type": "training"},
    )
    assert upload_resp.status_code == 201
    ds_id = upload_resp.json()["id"]

    # Validate
    validate_resp = await auth_client.post(f"/vault/datasets/{ds_id}/validate")
    assert validate_resp.status_code == 200
    assert validate_resp.json()["valid"] is True
    assert validate_resp.json()["record_count"] == 2

    # Preview
    preview_resp = await auth_client.get(f"/vault/datasets/{ds_id}/preview")
    assert preview_resp.status_code == 200
    assert len(preview_resp.json()["preview_records"]) == 2
    assert preview_resp.json()["preview_records"][0]["prompt"] == "hello"

    settings.vault_datasets_dir = original_dir
