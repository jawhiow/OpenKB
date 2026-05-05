from __future__ import annotations

import json
import os
import threading
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


def test_add_document_job_passes_job_object_for_internal_ocr_logs(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF",)
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_add_single_file(
        file_path,
        target_kb,
        *,
        strict=False,
        progress_callback=None,
        strategy_override=None,
        job=None,
    ):
        calls["job"] = job
        job.add_log("OCR page plan: 12 page(s) in 1 chunk(s)")

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        "/api/documents/add",
        json={"kb_dir": str(kb_dir), "path": str(source), "strategy_override": "ocr-local-long"},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert calls["job"] is job
    assert any(entry["message"] == "OCR page plan: 12 page(s) in 1 chunk(s)" for entry in job.logs)


def test_add_document_folder_continues_after_one_file_failure(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    source = tmp_path / "incoming"
    source.mkdir()
    good = source / "good.txt"
    bad = source / "bad.txt"
    good.write_text("ok", encoding="utf-8")
    bad.write_text("temporary gateway failure", encoding="utf-8")
    registry = JobRegistry()
    calls: list[Path] = []

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None):
        calls.append(file_path)
        assert target_kb == kb_dir
        assert strict is True
        if file_path.name == "bad.txt":
            raise RuntimeError("downstream 429")
        if progress_callback:
            progress_callback(f"Finished compile: {file_path.name}")

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
    assert job.status == "succeeded"
    assert calls == [bad, good]
    assert job.progress_current == 2
    assert job.progress_total == 2
    assert job.result == {
        "added": 1,
        "failed": 1,
        "total": 2,
        "failures": [
            {"name": "bad.txt", "path": str(bad), "error": "downstream 429"},
        ],
    }
    assert any(entry["level"] == "error" and "Failed bad.txt" in entry["message"] for entry in job.logs)
    assert any(entry["level"] == "warning" and "1 added and 1 failure" in entry["message"] for entry in job.logs)


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


def test_upload_document_accepts_multiple_files_in_one_add_job(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    calls: list[Path] = []

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None):
        calls.append(file_path)
        assert target_kb == kb_dir
        assert strict is True

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        f"/api/documents/upload?kb_dir={kb_dir}",
        files=[
            ("file", ("a.txt", b"first", "text/plain")),
            ("file", ("b.txt", b"second", "text/plain")),
        ],
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert calls == [kb_dir / "raw" / "a.txt", kb_dir / "raw" / "b.txt"]
    assert job.result["added"] == 2
    assert job.result["failed"] == 0
    assert job.result["total"] == 2


def test_document_detail_endpoint_returns_related_pages(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"abc123": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text(
        "---\ndoc_type: short\nfull_text: sources/paper.md\n---\n\n# Paper",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "Retrieval.md").write_text(
        "---\nsources: [summaries/paper.md]\n---\n\n# Retrieval",
        encoding="utf-8",
    )

    client = TestClient(create_app())
    response = client.get("/api/documents/paper", params={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "paper.pdf"
    assert body["related_count"] == 2
    assert body["related_pages"]["concepts"][0]["path"] == "concepts/Retrieval.md"


def test_delete_document_endpoint_runs_cleanup_job(tmp_path):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"%PDF")
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"abc123": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text(
        "---\ndoc_type: short\nfull_text: sources/paper.md\n---\n\n# Paper",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "Retrieval.md").write_text(
        "---\nsources: [summaries/paper.md]\n---\n\n# Retrieval",
        encoding="utf-8",
    )

    client = TestClient(create_app(registry=registry))
    response = client.delete("/api/documents/paper", params={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.type == "delete_source"
    assert job.result["document"]["name"] == "paper.pdf"
    assert job.result["removed_pages"] == ["summaries/paper.md", "concepts/Retrieval.md"]
    assert not (kb_dir / "raw" / "paper.pdf").exists()
    assert not (kb_dir / "wiki" / "concepts" / "Retrieval.md").exists()


def test_job_endpoints_can_stop_and_retry_jobs(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    source = tmp_path / "doc.txt"
    source.write_text("hello", encoding="utf-8")
    registry = JobRegistry()
    started = threading.Event()
    release = threading.Event()
    attempts: list[Path] = []

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None):
        attempts.append(file_path)
        if len(attempts) == 1:
            started.set()
            release.wait(timeout=2)
            return
        if progress_callback:
            progress_callback("Retry finished")

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        "/api/documents/add",
        json={"kb_dir": str(kb_dir), "path": str(source)},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    assert started.wait(timeout=2)

    stop_response = client.post(f"/api/jobs/{job_id}/stop")
    release.set()
    stopped = registry.wait(job_id, timeout=2)

    assert stop_response.status_code == 200
    assert stop_response.json()["status"] in {"running", "stopping", "stopped"}
    assert stopped is not None
    assert stopped.status == "stopped"

    retry_response = client.post(f"/api/jobs/{job_id}/retry")

    assert retry_response.status_code == 200
    retry_job_id = retry_response.json()["job"]["id"]
    retried = registry.wait(retry_job_id, timeout=2)
    assert retried is not None
    assert retried.status == "succeeded"
    assert retry_job_id != job_id
    assert attempts == [source, source]


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


def test_lint_fix_plan_job_matches_prioritized_report_items_and_includes_previews(tmp_path):
    kb_dir = _make_kb(tmp_path)
    for folder in ("companies", "industries", "themes", "metrics", "risks", "explorations"):
        (kb_dir / "wiki" / folder).mkdir()
    report_path = kb_dir / "wiki" / "reports" / "lint_20260503_143448.md"
    report_path.write_text(
        "# Lint Report - 20260503_143448\n\n"
        "## Semantic\n\n"
        "### 九、问题优先级汇总\n\n"
        "### 🔴 必须修复\n\n"
        "1. **填补 10+ 个死链接** - 将所有公司/概念页面中 `concepts/xxx` 引用改为已有页面名或创建对应概念页\n"
        "2. **创建 `concepts/云资本开支`** - 被 3 个核心公司页面引用\n"
        "3. **统一 Optical_Engines 为中文** - 保持全站语言一致\n\n"
        "### 🟡 建议修复\n\n"
        "4. **创建缺失的公司页面** - GUC、KYEC、ASE、Winway、MPI、GigaDevice、中芯国际\n"
        "5. **搭建 industries/themes/metrics/risks 目录** - 至少各创建 1-2 个种子页面\n"
        "6. **去冗余** - 明确 CoWoS/Advanced_Packaging、CPO/Optical_Engines 的页面边界\n"
        "7. **修复 Aspeed 评级日期** - 标注为首次评级日期\n\n"
        "### 6.1 缺失的高价值概念页\n\n"
        "| 建议创建的概念 | 理由 | 优先级 |\n"
        "|---|---|---|\n"
        "| **AI GPU（通用 AI 加速器）** | 报告标题关键词，ASIC 的对照物 | 🔴 高 |\n"
        "| **BMC 芯片与服务器管理** | Aspeed 页面引用的核心概念，无独立页面 | 🟡 中 |\n"
        "| **AI 推理 vs 训练** | 推理芯片 CAGR 68%，是结构性主题 | 🟡 中 |\n\n"
        "## Coverage Gap Candidates\n\n"
        "- [[concepts/Cloud_CAPEX]] - Cloud CAPEX (create)\n"
        "- [[concepts/Export_Controls]] - Export Controls (create)\n",
        encoding="utf-8",
    )
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/lint/fix-plan",
        json={"kb_dir": str(kb_dir), "report": "reports/lint_20260503_143448.md"},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["report"] == "reports/lint_20260503_143448.md"
    candidates = job.result["candidates"]
    names = [item["name"] for item in candidates]
    assert "Cloud_CAPEX" in names
    assert "GUC" in names
    assert "AI_GPU" in names
    assert "BMC_Server_Management" in names
    assert "AI_Inference_vs_Training" in names
    assert "Seed_industries" in names
    assert "Optical_Engines" in names
    assert "companies/aspeed.md" in [item["path"] for item in candidates]
    assert all(item.get("source_section") for item in candidates)
    assert all(item.get("reason") for item in candidates)
    assert all(item.get("preview") for item in candidates)
    assert next(item for item in candidates if item["name"] == "GUC")["path"] == "companies/GUC.md"
    assert next(item for item in candidates if item["name"] == "Seed_industries")["path"] == "industries/semiconductor-value-chain.md"


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


def test_lint_apply_fixes_job_creates_approved_company_and_schema_drafts(tmp_path):
    kb_dir = _make_kb(tmp_path)
    for folder in ("companies", "industries", "themes", "metrics", "risks"):
        (kb_dir / "wiki" / folder).mkdir()
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/lint/apply-fixes",
        json={
            "kb_dir": str(kb_dir),
            "candidates": [
                {
                    "name": "GUC",
                    "title": "GUC",
                    "path": "companies/GUC.md",
                    "action": "create",
                    "approved": True,
                    "reason": "创建缺失的公司页面",
                },
                {
                    "name": "Seed_industries",
                    "title": "Semiconductor Value Chain",
                    "path": "industries/semiconductor-value-chain.md",
                    "action": "create",
                    "approved": True,
                    "reason": "搭建 industries/themes/metrics/risks 目录",
                },
                {
                    "name": "Optical_Engines",
                    "title": "Optical Engines language normalization",
                    "path": "concepts/Optical_Engines.md",
                    "action": "manual-review",
                    "approved": True,
                },
            ],
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert [item["path"] for item in job.result["created"]] == [
        "companies/GUC.md",
        "industries/semiconductor-value-chain.md",
    ]
    assert "# GUC" in (kb_dir / "wiki" / "companies" / "GUC.md").read_text(encoding="utf-8")
    assert "status: draft" in (kb_dir / "wiki" / "industries" / "semiconductor-value-chain.md").read_text(encoding="utf-8")
    index = (kb_dir / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[companies/GUC]]" in index
    assert "[[industries/semiconductor-value-chain]]" in index


def test_lint_apply_fixes_job_returns_approved_manual_review_items_without_rewriting_pages(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "companies").mkdir()
    target = kb_dir / "wiki" / "companies" / "aspeed.md"
    target.write_text("# Aspeed\n\n评级日期 2025年6月9日\n", encoding="utf-8")
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/lint/apply-fixes",
        json={
            "kb_dir": str(kb_dir),
            "candidates": [
                {
                    "name": "Aspeed_Rating_Date",
                    "title": "Aspeed rating date clarification",
                    "path": "companies/aspeed.md",
                    "action": "manual-review",
                    "approved": True,
                    "reason": "修复 Aspeed 评级日期，标注为首次评级日期",
                    "preview": "Add clarification that 2025-06-09 is the initial rating date.",
                },
                {
                    "name": "Normalize_Concept_Links",
                    "title": "Normalize unresolved concept links",
                    "path": "wiki",
                    "action": "manual-review",
                    "approved": False,
                    "reason": "填补 10+ 个死链接",
                },
            ],
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["created"] == []
    assert job.result["reviewed"] == [
        {
            "name": "Aspeed_Rating_Date",
            "title": "Aspeed rating date clarification",
            "path": "companies/aspeed.md",
            "action": "manual-review",
            "reason": "修复 Aspeed 评级日期，标注为首次评级日期",
            "preview": "Add clarification that 2025-06-09 is the initial rating date.",
        }
    ]
    assert target.read_text(encoding="utf-8") == "# Aspeed\n\n评级日期 2025年6月9日\n"


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


def test_config_endpoint_can_create_and_switch_profiles(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("LLM_API_KEY=default-secret\n", encoding="utf-8")
    client = TestClient(create_app())

    create_response = client.put(
        "/api/config",
        json={
            "kb_dir": str(kb_dir),
            "create_profile": True,
            "profile_name": "Gateway",
            "model": "openai/doubao-seed-2-0-pro-260215",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "gateway-secret",
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["active_profile"] == "gateway"
    assert created["model"] == "openai/doubao-seed-2-0-pro-260215"
    assert [profile["id"] for profile in created["profiles"]] == ["default", "gateway"]
    assert created["api_key"] == "gateway-secret"
    assert created["profiles"][1]["api_key"] == "gateway-secret"

    switch_response = client.put(
        "/api/config",
        json={"kb_dir": str(kb_dir), "active_profile": "default"},
    )

    assert switch_response.status_code == 200
    switched = switch_response.json()
    assert switched["active_profile"] == "default"
    assert switched["model"] == "gpt-5.4-mini"


def test_config_endpoint_roundtrips_kb_scoped_ocr_settings(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("LLM_API_KEY=default-secret\nPADDLEOCR_TOKEN=paddle-secret\n", encoding="utf-8")
    runtime_dir = kb_dir / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "repo").mkdir()
    (runtime_dir / "python.exe").write_text("", encoding="utf-8")
    (runtime_dir / "run_pageindex.py").write_text("", encoding="utf-8")
    client = TestClient(create_app())

    get_response = client.get("/api/config", params={"kb_dir": str(kb_dir)})

    assert get_response.status_code == 200
    current = get_response.json()
    assert current["ocr_enabled"] is True
    assert current["ocr_default_model"] == "PaddleOCR-VL-1.5"
    assert current["ocr_chunk_pages"] == 100
    assert current["paddleocr_token"] == "paddle-secret"
    assert current["pageindex_local_enabled"] is False
    assert current["pageindex_local_repo_dir"] == ""
    assert current["pageindex_local_python_path"] == ""
    assert current["pageindex_local_script_path"] == ""

    update_response = client.put(
        "/api/config",
        json={
            "kb_dir": str(kb_dir),
            "ocr_enabled": False,
            "ocr_detection_mode": "always_ask",
            "ocr_default_model": "PP-StructureV3",
            "ocr_chunk_pages": 60,
            "ocr_auto_recommend": False,
            "paddleocr_token": "new-paddle-token",
            "pageindex_local_enabled": True,
            "pageindex_local_model": "gpt-5.4",
            "pageindex_local_installation_state": "installed",
            "pageindex_local_repo_dir": str(runtime_dir / "repo"),
            "pageindex_local_python_path": str(runtime_dir / "python.exe"),
            "pageindex_local_script_path": str(runtime_dir / "run_pageindex.py"),
        },
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["ocr_enabled"] is False
    assert updated["ocr_detection_mode"] == "always_ask"
    assert updated["ocr_default_model"] == "PP-StructureV3"
    assert updated["ocr_chunk_pages"] == 60
    assert updated["ocr_auto_recommend"] is False
    assert updated["paddleocr_token"] == "new-paddle-token"
    assert updated["pageindex_local_enabled"] is True
    assert updated["pageindex_local_model"] == "gpt-5.4"
    assert updated["pageindex_local_installation_state"] == "installed"
    assert updated["pageindex_local_repo_dir"] == str(runtime_dir / "repo")
    assert updated["pageindex_local_python_path"] == str(runtime_dir / "python.exe")
    assert updated["pageindex_local_script_path"] == str(runtime_dir / "run_pageindex.py")
    env_text = (kb_dir / ".env").read_text(encoding="utf-8")
    assert "PADDLEOCR_TOKEN=new-paddle-token\n" in env_text
    manifest = json.loads((kb_dir / ".openkb" / "pageindex-local" / "installation.json").read_text(encoding="utf-8"))
    assert manifest == {
        "repo_dir": str(runtime_dir / "repo"),
        "python_path": str(runtime_dir / "python.exe"),
        "script_path": str(runtime_dir / "run_pageindex.py"),
    }


def test_config_endpoint_exports_and_imports_llm_profiles_with_keys(tmp_path):
    source = _make_kb(tmp_path / "source")
    (source / ".env").write_text("LLM_API_KEY=default-secret\n", encoding="utf-8")
    runtime_dir = source / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "repo").mkdir()
    (runtime_dir / "python.exe").write_text("", encoding="utf-8")
    (runtime_dir / "run_pageindex.py").write_text("", encoding="utf-8")
    target = _make_kb(tmp_path / "target")
    client = TestClient(create_app())

    create_response = client.put(
        "/api/config",
        json={
            "kb_dir": str(source),
            "create_profile": True,
            "profile_name": "Gateway",
            "model": "openai/doubao-seed-2-0-pro-260215",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "compile_max_concurrency": 3,
            "ocr_enabled": False,
            "ocr_detection_mode": "always_ask",
            "ocr_default_model": "PP-StructureV3",
            "ocr_chunk_pages": 60,
            "ocr_auto_recommend": False,
            "paddleocr_token": "paddle-secret",
            "pageindex_local_enabled": True,
            "pageindex_local_model": "gpt-5.4",
            "pageindex_local_installation_state": "installed",
            "pageindex_local_repo_dir": str(runtime_dir / "repo"),
            "pageindex_local_python_path": str(runtime_dir / "python.exe"),
            "pageindex_local_script_path": str(runtime_dir / "run_pageindex.py"),
            "api_key": "gateway-secret",
        },
    )
    assert create_response.status_code == 200

    export_response = client.get("/api/config/export", params={"kb_dir": str(source)})

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported["format"] == "openkb.settings-config.v1"
    assert exported["settings"]["compile_max_concurrency"] == 3
    assert exported["settings"]["ocr_enabled"] is False
    assert exported["settings"]["ocr_detection_mode"] == "always_ask"
    assert exported["settings"]["ocr_default_model"] == "PP-StructureV3"
    assert exported["settings"]["ocr_chunk_pages"] == 60
    assert exported["settings"]["ocr_auto_recommend"] is False
    assert exported["settings"]["paddleocr_token"] == "paddle-secret"
    assert exported["settings"]["pageindex_local_enabled"] is True
    assert exported["settings"]["pageindex_local_model"] == "gpt-5.4"
    assert exported["settings"]["pageindex_local_installation_state"] == "installed"
    assert exported["settings"]["pageindex_local_repo_dir"] == str(runtime_dir / "repo")
    assert exported["settings"]["pageindex_local_python_path"] == str(runtime_dir / "python.exe")
    assert exported["settings"]["pageindex_local_script_path"] == str(runtime_dir / "run_pageindex.py")
    assert exported["profiles"][0]["api_key"] == "default-secret"
    assert exported["profiles"][1]["api_key"] == "gateway-secret"

    import_response = client.post(
        "/api/config/import",
        json={"kb_dir": str(target), "config": exported},
    )

    assert import_response.status_code == 200
    imported = import_response.json()
    assert imported["active_profile"] == "gateway"
    assert imported["compile_max_concurrency"] == 3
    assert imported["ocr_enabled"] is False
    assert imported["ocr_detection_mode"] == "always_ask"
    assert imported["ocr_default_model"] == "PP-StructureV3"
    assert imported["ocr_chunk_pages"] == 60
    assert imported["ocr_auto_recommend"] is False
    assert imported["paddleocr_token"] == "paddle-secret"
    assert imported["pageindex_local_enabled"] is True
    assert imported["pageindex_local_model"] == "gpt-5.4"
    assert imported["pageindex_local_installation_state"] == "installed"
    assert imported["pageindex_local_repo_dir"] == str(runtime_dir / "repo")
    assert imported["pageindex_local_python_path"] == str(runtime_dir / "python.exe")
    assert imported["pageindex_local_script_path"] == str(runtime_dir / "run_pageindex.py")
    assert [profile["id"] for profile in imported["profiles"]] == ["default", "gateway"]
    assert imported["api_key"] == "gateway-secret"
    assert imported["profiles"][1]["api_key"] == "gateway-secret"
    target_manifest = json.loads((target / ".openkb" / "pageindex-local" / "installation.json").read_text(encoding="utf-8"))
    assert target_manifest == {
        "repo_dir": str(runtime_dir / "repo"),
        "python_path": str(runtime_dir / "python.exe"),
        "script_path": str(runtime_dir / "run_pageindex.py"),
    }


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
