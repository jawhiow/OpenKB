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
    (kb_dir / "wiki" / "reports").mkdir(parents=True)
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")
    (kb_dir / "wiki" / "AGENTS.md").write_text("Schema", encoding="utf-8")
    (kb_dir / ".openkb").mkdir()
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\nlanguage: zh\npageindex_threshold: 20\nwire_api: responses\n",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({}), encoding="utf-8")
    return kb_dir


def test_add_document_job_uses_strict_add_and_records_stage_logs(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    source = tmp_path / "doc.txt"
    source.write_text("hello", encoding="utf-8")
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None):
        calls["file_path"] = file_path
        calls["target_kb"] = target_kb
        calls["strict"] = strict
        if progress_callback:
            progress_callback("Compiling short document: doc.txt")
        raise RuntimeError("llm timeout")

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        "/api/documents/add",
        json={"kb_dir": str(kb_dir), "path": str(source)},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "failed"
    assert job.error == "llm timeout"
    assert calls["file_path"] == source
    assert calls["target_kb"] == kb_dir
    assert calls["strict"] is True
    assert any(entry["message"] == "Compiling short document: doc.txt" for entry in job.logs)
    assert job.progress_current == 0
    assert job.progress_total == 1


def test_upload_document_accepts_single_file_and_queues_add_job(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None):
        calls["file_path"] = file_path
        calls["target_kb"] = target_kb
        calls["strict"] = strict

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        f"/api/documents/upload?kb_dir={kb_dir}",
        files={"file": ("doc.txt", b"hello upload", "text/plain")},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert calls["file_path"] == kb_dir / "raw" / "doc.txt"
    assert calls["target_kb"] == kb_dir
    assert calls["strict"] is True
