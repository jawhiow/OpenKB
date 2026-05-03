from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

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


def test_lint_fix_plan_job_extracts_candidates_without_writing_pages(tmp_path):
    kb_dir = _make_kb(tmp_path)
    report_path = kb_dir / "wiki" / "reports" / "lint.md"
    report_path.write_text(
        "## Gaps\n"
        "- Missing concept page for AI CPU and global AI GPU.\n"
        "- Export Controls are not covered.\n",
        encoding="utf-8",
    )
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/lint/fix-plan",
        json={"kb_dir": str(kb_dir), "report": "reports/lint.md"},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["report"] == "reports/lint.md"
    assert [item["name"] for item in job.result["candidates"]] == [
        "AI_CPU",
        "AI_GPU",
        "Export_Controls",
    ]
    assert not (kb_dir / "wiki" / "concepts" / "AI_CPU.md").exists()
    assert not (kb_dir / "wiki" / "concepts" / "AI_GPU.md").exists()


def test_lint_fix_plan_job_rejects_reports_outside_reports_dir(tmp_path):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/lint/fix-plan",
        json={"kb_dir": str(kb_dir), "report": "index.md"},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "failed"
    assert "wiki/reports" in str(job.error)


def test_lint_apply_fixes_job_creates_only_explicitly_approved_draft_pages(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "concepts" / "AI_GPU.md").write_text("# Existing AI GPU", encoding="utf-8")
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/lint/apply-fixes",
        json={
            "kb_dir": str(kb_dir),
            "candidates": [
                {"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "create", "approved": True},
                {"name": "AI_GPU", "title": "AI GPU", "path": "concepts/AI_GPU.md", "action": "create", "approved": True},
                {"name": "Cloud_CAPEX", "title": "Cloud CAPEX", "path": "concepts/Cloud_CAPEX.md", "action": "create"},
            ],
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["created"] == [
        {"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "created"}
    ]
    ai_cpu = (kb_dir / "wiki" / "concepts" / "AI_CPU.md").read_text(encoding="utf-8")
    assert "status: draft" in ai_cpu
    assert "# AI CPU" in ai_cpu
    assert (kb_dir / "wiki" / "concepts" / "AI_GPU.md").read_text(encoding="utf-8") == "# Existing AI GPU"
    assert not (kb_dir / "wiki" / "concepts" / "Cloud_CAPEX.md").exists()


def test_lint_apply_fixes_job_ignores_unapproved_candidates(tmp_path):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/lint/apply-fixes",
        json={
            "kb_dir": str(kb_dir),
            "candidates": [
                {"name": "AI_CPU", "title": "AI CPU", "approved": True},
                {"name": "AI_GPU", "title": "AI GPU", "approved": False},
            ],
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert [item["name"] for item in job.result["created"]] == ["AI_CPU"]
    assert (kb_dir / "wiki" / "concepts" / "AI_CPU.md").exists()
    assert not (kb_dir / "wiki" / "concepts" / "AI_GPU.md").exists()


def test_test_llm_endpoint_uses_current_form_values(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("LLM_API_KEY=saved-secret\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_completion(model, messages, **kwargs):
        captured["model"] = model
        captured["message"] = messages[0]["content"]
        captured["wire_api"] = os.environ.get("OPENKB_WIRE_API")
        captured["base_url"] = os.environ.get("OPENAI_BASE_URL")
        captured["api_key"] = os.environ.get("OPENAI_API_KEY")
        captured["custom_llm_provider"] = kwargs.get("custom_llm_provider")
        return SimpleNamespace(text="pong", usage=SimpleNamespace())

    monkeypatch.setattr("openkb.llm_runtime.completion", fake_completion)

    client = TestClient(create_app())
    response = client.post(
        "/api/config/test-llm",
        json={
            "kb_dir": str(kb_dir),
            "model": "openai/doubao-seed-2-0-pro-260215",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "override-secret",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["message"] == "LLM test succeeded."
    assert captured["model"] == "doubao-seed-2-0-pro-260215"
    assert captured["custom_llm_provider"] == "custom_openai"
    assert body["effective_model"] == "doubao-seed-2-0-pro-260215"
    assert body["effective_url"] == "https://gateway.example.com/v1/chat/completions"
    assert captured["wire_api"] == "chat_completions"
    assert captured["base_url"] == "https://gateway.example.com/v1"
    assert captured["api_key"] == "override-secret"
    assert "ping" in str(captured["message"]).lower()


def test_test_llm_endpoint_returns_rich_error_context(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)

    def fake_completion(model, messages, **kwargs):
        raise RuntimeError("blocked by upstream")

    monkeypatch.setattr("openkb.llm_runtime.completion", fake_completion)

    client = TestClient(create_app())
    response = client.post(
        "/api/config/test-llm",
        json={
            "kb_dir": str(kb_dir),
            "model": "openai/doubao-seed-2-0-pro-260215",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "override-secret",
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "blocked by upstream" in detail
    assert "effective_model: doubao-seed-2-0-pro-260215" in detail
    assert "effective_wire_api: chat_completions" in detail
    assert "effective_url: https://gateway.example.com/v1/chat/completions" in detail
    assert "api_key: over...cret" in detail
    assert "override-secret" not in detail
