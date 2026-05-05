from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from openkb.client.jobs import JobRegistry
from openkb.client.server import create_app


def _make_kb(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "kb"
    (kb_dir / "raw").mkdir(parents=True)
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / ".openkb").mkdir()
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "pageindex_local_enabled: true\n"
        "pageindex_local_installation_state: installed\n",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text("{}", encoding="utf-8")
    return kb_dir


def test_ocr_cache_endpoint_lists_cache_entries_without_secrets(tmp_path):
    kb_dir = _make_kb(tmp_path)
    cache_dir = kb_dir / ".openkb" / "ocr" / "cache" / "abc123"
    normalized_dir = cache_dir / "normalized"
    normalized_dir.mkdir(parents=True)
    (normalized_dir / "pages.json").write_text('[{"page": 1, "content": "OCR text", "images": []}]', encoding="utf-8")
    (normalized_dir / "pageindex_input.md").write_text("## Page 1\n\nOCR text", encoding="utf-8")
    (cache_dir / "manifest.json").write_text(
        json.dumps(
            {
                "file_hash": "abc123",
                "status": "ready",
                "doc_name": "scan",
                "page_count": 12,
                "ocr_model": "PaddleOCR-VL-1.5",
            }
        ),
        encoding="utf-8",
    )
    (kb_dir / ".env").write_text("PADDLEOCR_TOKEN=secret-token\n", encoding="utf-8")

    client = TestClient(create_app())
    response = client.get("/api/ocr/cache", params={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == [
        {
            "file_hash": "abc123",
            "status": "ready",
            "doc_name": "scan",
            "page_count": 12,
            "ocr_model": "PaddleOCR-VL-1.5",
            "has_pages": True,
            "has_pageindex_input": True,
        }
    ]
    assert "secret-token" not in json.dumps(body)


def test_pageindex_local_status_endpoint_reports_runtime_readiness(tmp_path):
    kb_dir = _make_kb(tmp_path)
    runtime_root = kb_dir / ".openkb" / "pageindex-local"
    runtime_dir = runtime_root / "runtime"
    runtime_dir.mkdir(parents=True)
    python_path = runtime_dir / "python.exe"
    script_path = runtime_dir / "run_pageindex.py"
    python_path.write_text("", encoding="utf-8")
    script_path.write_text("", encoding="utf-8")
    (runtime_root / "installation.json").write_text(
        json.dumps(
            {
                "repo_dir": str(runtime_dir),
                "python_path": str(python_path),
                "script_path": str(script_path),
                "version": "test-version",
            }
        ),
        encoding="utf-8",
    )

    client = TestClient(create_app())
    response = client.get("/api/pageindex-local/status", params={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["ready"] is True
    assert body["installation_state"] == "installed"
    assert body["manifest"]["version"] == "test-version"


def test_add_document_endpoint_passes_strategy_override_to_add_job(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF")
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None, strategy_override=None):
        calls["file_path"] = file_path
        calls["target_kb"] = target_kb
        calls["strict"] = strict
        calls["strategy_override"] = strategy_override

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        "/api/documents/add",
        json={"kb_dir": str(kb_dir), "path": str(source), "strategy_override": "ocr-pageindex-local"},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)
    assert job is not None
    assert job.status == "succeeded"
    assert calls["file_path"] == source
    assert calls["target_kb"] == kb_dir
    assert calls["strict"] is True
    assert calls["strategy_override"] == "ocr-pageindex-local"


def test_upload_document_endpoint_passes_strategy_override_to_add_job(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None, strategy_override=None):
        calls["file_path"] = file_path
        calls["target_kb"] = target_kb
        calls["strict"] = strict
        calls["strategy_override"] = strategy_override

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        f"/api/documents/upload?kb_dir={kb_dir}&strategy_override=ocr-local-long",
        files={"file": ("scan.pdf", b"%PDF", "application/pdf")},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)
    assert job is not None
    assert job.status == "succeeded"
    assert calls["file_path"] == kb_dir / "raw" / "scan.pdf"
    assert calls["target_kb"] == kb_dir
    assert calls["strict"] is True
    assert calls["strategy_override"] == "ocr-local-long"


def test_ocr_cache_invalidate_marks_manifest_invalidated(tmp_path):
    kb_dir = _make_kb(tmp_path)
    cache_dir = kb_dir / ".openkb" / "ocr" / "cache" / "abc123"
    cache_dir.mkdir(parents=True)
    (cache_dir / "manifest.json").write_text(
        json.dumps({"file_hash": "abc123", "status": "ready", "doc_name": "scan"}),
        encoding="utf-8",
    )

    client = TestClient(create_app())
    response = client.post(
        "/api/ocr/cache/abc123/invalidate",
        json={"kb_dir": str(kb_dir)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entry"]["status"] == "invalidated"
    manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "invalidated"
    assert manifest["invalidated_at"]


def test_ocr_cache_rerun_queues_add_job_for_registered_raw_file(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    raw = kb_dir / "raw" / "scan.pdf"
    raw.write_bytes(b"%PDF")
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"abc123": {"name": "scan.pdf", "type": "long_pdf"}}),
        encoding="utf-8",
    )
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None, strategy_override=None, force=False):
        calls["file_path"] = file_path
        calls["target_kb"] = target_kb
        calls["strict"] = strict
        calls["strategy_override"] = strategy_override
        calls["force"] = force

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        "/api/ocr/cache/abc123/rerun",
        json={"kb_dir": str(kb_dir), "strategy_override": "ocr-pageindex-local"},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)
    assert job is not None
    assert job.status == "succeeded"
    assert calls["file_path"] == raw
    assert calls["target_kb"] == kb_dir
    assert calls["strict"] is True
    assert calls["force"] is True
    assert calls["strategy_override"] == "ocr-pageindex-local"
