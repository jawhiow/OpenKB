from __future__ import annotations

from importlib import import_module
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from agents import RawResponsesStreamEvent
from openai.types.responses import ResponseTextDeltaEvent

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


def _seed_llm_usage_rows(kb_dir: Path) -> None:
    usage = import_module("openkb.llm_usage")
    usage.record_usage(
        kb_dir=kb_dir,
        feature="compile",
        model="gpt-4o-mini",
        wire_api="responses",
        base_url="",
        status="succeeded",
        duration_ms=210,
        error="",
        input_payload={"messages": [{"role": "user", "content": "compile"}]},
        output_payload={"text": "compiled"},
        created_at="2026-05-06T10:00:00Z",
    )
    usage.record_usage(
        kb_dir=kb_dir,
        feature="query",
        model="gpt-5.4",
        wire_api="responses",
        base_url="",
        status="succeeded",
        duration_ms=90,
        error="",
        input_payload={"messages": [{"role": "user", "content": "who won"}]},
        output_payload={"text": "answer"},
        created_at="2026-05-06T10:01:00Z",
    )
    usage.record_usage(
        kb_dir=kb_dir,
        feature="query",
        model="gpt-5.4",
        wire_api="responses",
        base_url="",
        status="failed",
        duration_ms=120,
        error="upstream timeout",
        input_payload={"messages": [{"role": "user", "content": "why failed"}]},
        output_payload={"text": ""},
        created_at="2026-05-06T10:02:00Z",
    )


def test_query_job_creates_persisted_chat_session_for_web_ask(tmp_path):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))
    result = MagicMock()
    result.final_output = "first answer"
    result.to_input_list.return_value = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]

    with (
        patch("openkb.cli._setup_llm_key"),
        patch("openkb.agent.query.Runner.run", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = result
        response = client.post(
            "/api/query",
            json={"kb_dir": str(kb_dir), "question": "first question"},
        )
        assert response.status_code == 200
        job_id = response.json()["job"]["id"]
        job = registry.wait(job_id, timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["answer"] == "first answer"
    assert job.result["session"]["id"]
    assert job.result["session_id"] == job.result["session"]["id"]
    assert "references" in job.result
    session_path = kb_dir / ".openkb" / "chats" / f"{job.result['session_id']}.json"
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["title"] == "first question"
    assert saved["turn_count"] == 1
    assert saved["user_turns"] == ["first question"]
    assert saved["assistant_texts"] == ["first answer"]
    assert mock_run.call_args.args[1] == [
        {"role": "user", "content": "first question"}
    ]


def test_llm_usage_endpoints_support_list_search_pagination_and_export(tmp_path):
    kb_dir = _make_kb(tmp_path)
    _seed_llm_usage_rows(kb_dir)
    client = TestClient(create_app())

    listed = client.get(
        "/api/llm-usage",
        params={"kb_dir": str(kb_dir), "page": 1, "page_size": 2},
    )

    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["feature"] == "query"
    assert body["items"][0]["status"] == "failed"
    assert "input_payload" not in body["items"][0]
    assert "output_payload" not in body["items"][0]

    searched = client.get(
        "/api/llm-usage",
        params={"kb_dir": str(kb_dir), "q": "timeout"},
    )

    assert searched.status_code == 200
    searched_body = searched.json()
    assert searched_body["total"] == 1
    assert searched_body["items"][0]["error"] == "upstream timeout"

    exported = client.get(
        "/api/llm-usage/export",
        params={"kb_dir": str(kb_dir), "q": "query"},
    )

    assert exported.status_code == 200
    export_body = exported.json()
    assert len(export_body["items"]) == 2
    assert export_body["items"][0]["input_payload"]["messages"][0]["content"] == "why failed"
    assert export_body["items"][0]["output_payload"]["text"] == ""


def test_llm_usage_excludes_llm_test_and_model_pool_probe(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    usage = import_module("openkb.llm_usage")
    client = TestClient(create_app())

    def fake_completion(model, messages, **kwargs):
        return SimpleNamespace(text="pong")

    monkeypatch.setattr("openkb.llm_runtime.completion", fake_completion)

    llm_test = client.post(
        "/api/config/test-llm",
        json={"kb_dir": str(kb_dir), "model": "gpt-5.4-mini"},
    )
    probe = client.post(
        "/api/model-pool/probe",
        json={"kb_dir": str(kb_dir)},
    )

    assert llm_test.status_code == 200
    assert probe.status_code == 200
    assert usage.export_usage(kb_dir) == []


def test_query_job_resumes_existing_web_chat_session(tmp_path):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))
    first = MagicMock()
    first.final_output = "first answer"
    first.to_input_list.return_value = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]
    second = MagicMock()
    second.final_output = "second answer"
    second.to_input_list.return_value = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow up"},
        {"role": "assistant", "content": "second answer"},
    ]

    with (
        patch("openkb.cli._setup_llm_key"),
        patch("openkb.agent.query.Runner.run", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.side_effect = [first, second]
        first_response = client.post(
            "/api/query",
            json={"kb_dir": str(kb_dir), "question": "first question"},
        )
        first_job = registry.wait(first_response.json()["job"]["id"], timeout=2)
        response = client.post(
            "/api/query",
            json={
                "kb_dir": str(kb_dir),
                "question": "follow up",
                "session_id": first_job.result["session_id"],
            },
        )
        assert response.status_code == 200
        job = registry.wait(response.json()["job"]["id"], timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["answer"] == "second answer"
    assert job.result["session_id"] == first_job.result["session_id"]
    assert job.result["session"]["turn_count"] == 2
    saved = json.loads(
        (kb_dir / ".openkb" / "chats" / f"{job.result['session_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["user_turns"] == ["first question", "follow up"]
    assert saved["assistant_texts"] == ["first answer", "second answer"]
    assert mock_run.call_args_list[1].args[1] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow up"},
    ]


def test_query_stream_returns_delta_done_session_and_references(tmp_path):
    kb_dir = _make_kb(tmp_path)
    client = TestClient(create_app())

    class FakeStreamResult:
        final_output = "Hello world"

        async def stream_events(self):
            yield RawResponsesStreamEvent(
                data=ResponseTextDeltaEvent(
                    content_index=0,
                    delta="Hello",
                    item_id="item_1",
                    logprobs=[],
                    output_index=0,
                    sequence_number=1,
                    type="response.output_text.delta",
                )
            )
            yield RawResponsesStreamEvent(
                data=ResponseTextDeltaEvent(
                    content_index=0,
                    delta=" world",
                    item_id="item_1",
                    logprobs=[],
                    output_index=0,
                    sequence_number=2,
                    type="response.output_text.delta",
                )
            )

        def to_input_list(self):
            return [
                {"role": "user", "content": "stream me"},
                {"role": "assistant", "content": "Hello world"},
            ]

    with (
        patch("openkb.cli._setup_llm_key"),
        patch("openkb.agent.query.Runner.run_streamed", return_value=FakeStreamResult()),
        patch("openkb.agent.query.QueryReferenceTracker.references") as refs,
    ):
        refs.return_value = [{"type": "wiki_file", "path": "index.md"}]
        response = client.post(
            "/api/query/stream",
            json={"kb_dir": str(kb_dir), "question": "stream me"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert 'event: delta\ndata: {"text":"Hello"}' in body
    assert 'event: delta\ndata: {"text":" world"}' in body
    done_payload = json.loads(body.split("event: done\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert done_payload["answer"] == "Hello world"
    assert done_payload["references"] == [{"type": "wiki_file", "path": "index.md"}]
    assert done_payload["session"]["turn_count"] == 1
    assert done_payload["session_id"] == done_payload["session"]["id"]


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


def test_model_pool_endpoint_merges_profiles_with_health_status(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "language: zh\n"
        "wire_api: chat_completions\n"
        "llm_profiles:\n"
        "  - id: gateway\n"
        "    name: Gateway\n"
        "    model: gpt-4o-mini\n"
        "    wire_api: chat_completions\n"
        "    base_url: https://gateway.example.com/v1\n"
        "    tags: [GPT, Fast]\n"
        "    features: [chat]\n"
        "    enabled: true\n"
        "    probe_models: [gpt-4o-mini, gpt-5.4-mini]\n"
        "active_llm_profile: gateway\n",
        encoding="utf-8",
    )
    status_dir = kb_dir / ".openkb" / "model-pool"
    status_dir.mkdir(parents=True)
    (status_dir / "status.json").write_text(
        json.dumps(
            {
                "profiles": {
                    "gateway": {
                        "health": "degraded",
                        "last_checked_at": "2026-05-06T11:42:08Z",
                        "latency_ms": 812,
                        "consecutive_failures": 1,
                        "available_models": ["gpt-4o-mini"],
                        "failed_models": {"gpt-5.4-mini": "model_not_found"},
                        "last_error": "gpt-5.4-mini: model_not_found",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/api/model-pool", params={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["active_profile"] == "gateway"
    assert body["summary"]["total"] == 1
    assert body["summary"]["degraded"] == 1
    card = body["profiles"][0]
    assert card["id"] == "gateway"
    assert card["name"] == "Gateway"
    assert card["base_url"] == "https://gateway.example.com/v1"
    assert card["tags"] == ["GPT", "Fast"]
    assert card["features"] == ["chat"]
    assert card["probe_models"] == ["gpt-4o-mini", "gpt-5.4-mini"]
    assert card["health"] == "degraded"
    assert card["available_models"] == ["gpt-4o-mini"]
    assert card["failed_models"] == {"gpt-5.4-mini": "model_not_found"}


def test_model_pool_profile_probe_persists_status_without_deleting_config(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "language: zh\n"
        "wire_api: chat_completions\n"
        "llm_profiles:\n"
        "  - id: gateway\n"
        "    name: Gateway\n"
        "    model: gpt-4o-mini\n"
        "    wire_api: chat_completions\n"
        "    base_url: https://gateway.example.com/v1\n"
        "    enabled: true\n"
        "    probe_models: [gpt-4o-mini, missing-model]\n"
        "active_llm_profile: gateway\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_probe(_kb_dir, route, **_kwargs):
        calls.append({"model": route.model, "base_url": route.base_url})
        if route.model == "missing-model":
            raise RuntimeError("model_not_found")
        return {"ok": True, "latency_ms": 25}

    monkeypatch.setattr("openkb.model_pool.probe_model_route", fake_probe)
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/model-pool/profiles/gateway/probe",
        json={"kb_dir": str(kb_dir)},
    )

    assert response.status_code == 200
    assert registry.list_jobs() == []
    assert calls[0]["model"] == "gpt-4o-mini"
    assert calls[0]["base_url"] == "https://gateway.example.com/v1"
    assert calls[1]["model"] == "missing-model"
    result = response.json()["profile"]
    assert result["health"] == "degraded"
    assert result["available_models"] == ["gpt-4o-mini"]
    assert result["failed_models"] == {"missing-model": "model_not_found"}
    status = json.loads((kb_dir / ".openkb" / "model-pool" / "status.json").read_text(encoding="utf-8"))
    assert status["profiles"]["gateway"]["health"] == "degraded"
    config = (kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8")
    assert "id: gateway" in config
    assert "missing-model" in config


def test_model_pool_probe_returns_direct_result_without_job(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "language: zh\n"
        "wire_api: chat_completions\n"
        "llm_profiles:\n"
        "  - id: gateway\n"
        "    name: Gateway\n"
        "    model: gpt-4o-mini\n"
        "    wire_api: chat_completions\n"
        "    base_url: https://gateway.example.com/v1\n"
        "    enabled: true\n"
        "    models:\n"
        "      - name: gpt-4o-mini\n"
        "        weight: 100\n"
        "      - name: missing-model\n"
        "        weight: 50\n"
        "active_llm_profile: gateway\n",
        encoding="utf-8",
    )

    def fake_probe(_kb_dir, route, **_kwargs):
        if route.model == "missing-model":
            raise RuntimeError("model_not_found")
        return {"ok": True, "latency_ms": 25}

    monkeypatch.setattr("openkb.model_pool.probe_model_route", fake_probe)
    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))

    response = client.post(
        "/api/model-pool/profiles/gateway/probe",
        json={"kb_dir": str(kb_dir)},
    )

    assert response.status_code == 200
    body = response.json()
    assert "job" not in body
    assert registry.list_jobs() == []
    assert body["profile"]["health"] == "degraded"
    routes = {route["model"]: route for route in body["profile"]["routes"]}
    assert routes["gpt-4o-mini"]["health"] == "healthy"
    assert routes["gpt-4o-mini"]["weight"] == 100
    assert routes["missing-model"]["health"] == "offline"
    assert routes["missing-model"]["last_error"] == "model_not_found"


def test_model_pool_profile_crud_supports_multiple_models(tmp_path):
    kb_dir = _make_kb(tmp_path)
    client = TestClient(create_app())

    created = client.post(
        "/api/model-pool/profiles",
        json={
            "kb_dir": str(kb_dir),
            "name": "Gateway",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "gateway-secret",
            "models": [
                {"name": "fast-model", "weight": 2},
                {"name": "slow-model", "weight": 1},
            ],
        },
    )

    assert created.status_code == 200
    pool = created.json()["model_pool"]
    gateway = next(profile for profile in pool["profiles"] if profile["name"] == "Gateway")
    assert [route["model"] for route in gateway["routes"]] == ["fast-model", "slow-model"]
    assert [route["weight"] for route in gateway["routes"]] == [2, 1]
    assert gateway["api_key_configured"] is True

    updated = client.put(
        f"/api/model-pool/profiles/{gateway['id']}",
        json={
            "kb_dir": str(kb_dir),
            "name": "Gateway Updated",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v2",
            "models": [
                {"name": "fast-model", "weight": 3},
                {"name": "backup-model", "weight": 1},
            ],
        },
    )

    assert updated.status_code == 200
    gateway = next(profile for profile in updated.json()["model_pool"]["profiles"] if profile["id"] == gateway["id"])
    assert gateway["name"] == "Gateway Updated"
    assert gateway["base_url"] == "https://gateway.example.com/v2"
    assert [route["model"] for route in gateway["routes"]] == ["fast-model", "backup-model"]
    assert [route["weight"] for route in gateway["routes"]] == [3, 1]

    deleted = client.request(
        "DELETE",
        f"/api/model-pool/profiles/{gateway['id']}",
        json={"kb_dir": str(kb_dir)},
    )

    assert deleted.status_code == 200
    assert gateway["id"] not in [profile["id"] for profile in deleted.json()["model_pool"]["profiles"]]
    config = (kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8")
    assert "Gateway Updated" not in config


def test_model_pool_routes_are_model_level_and_weighted_round_robin(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "language: zh\n"
        "wire_api: chat_completions\n"
        "model_pool:\n"
        "  enabled: true\n"
        "  strategy: weighted_round_robin\n"
        "llm_profiles:\n"
        "  - id: gateway\n"
        "    name: Gateway\n"
        "    model: fallback-model\n"
        "    wire_api: chat_completions\n"
        "    base_url: https://gateway.example.com/v1\n"
        "    enabled: true\n"
        "    models:\n"
        "      - name: fast-model\n"
        "        weight: 2\n"
        "      - name: slow-model\n"
        "        weight: 1\n"
        "active_llm_profile: gateway\n",
        encoding="utf-8",
    )
    from openkb.model_pool import record_route_success, select_model_route

    record_route_success(kb_dir, "gateway", "fast-model", latency_ms=10)
    record_route_success(kb_dir, "gateway", "slow-model", latency_ms=20)

    first = select_model_route(kb_dir)
    second = select_model_route(kb_dir)
    third = select_model_route(kb_dir)
    fourth = select_model_route(kb_dir)

    assert [first.model, second.model, third.model, fourth.model] == [
        "fast-model",
        "fast-model",
        "slow-model",
        "fast-model",
    ]


def test_query_job_uses_model_pool_and_retries_after_runtime_failure(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: fallback-model\n"
        "language: zh\n"
        "wire_api: chat_completions\n"
        "model_pool:\n"
        "  enabled: true\n"
        "  strategy: weighted_round_robin\n"
        "llm_profiles:\n"
        "  - id: primary\n"
        "    name: Primary\n"
        "    model: bad-model\n"
        "    wire_api: chat_completions\n"
        "    base_url: https://bad.example.com/v1\n"
        "    enabled: true\n"
        "    models:\n"
        "      - name: bad-model\n"
        "        weight: 100\n"
        "  - id: backup\n"
        "    name: Backup\n"
        "    model: good-model\n"
        "    wire_api: chat_completions\n"
        "    base_url: https://good.example.com/v1\n"
        "    enabled: true\n"
        "    models:\n"
        "      - name: good-model\n"
        "        weight: 100\n"
        "active_llm_profile: primary\n",
        encoding="utf-8",
    )
    from openkb.model_pool import record_route_success

    record_route_success(kb_dir, "primary", "bad-model", latency_ms=10)
    record_route_success(kb_dir, "backup", "good-model", latency_ms=20)

    registry = JobRegistry()
    client = TestClient(create_app(registry=registry))
    calls: list[str] = []

    async def fake_run(agent, message, **kwargs):
        calls.append(agent.model)
        if "bad-model" in agent.model:
            raise RuntimeError("upstream 500")
        result = MagicMock()
        result.final_output = "backup answer"
        result.to_input_list.return_value = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "backup answer"},
        ]
        return result

    with (
        patch("openkb.cli._setup_llm_key"),
        patch("openkb.agent.query.Runner.run", side_effect=fake_run),
        patch("openkb.model_pool.probe_model_route", side_effect=RuntimeError("upstream 500")) as probe,
    ):
        response = client.post(
            "/api/query",
            json={"kb_dir": str(kb_dir), "question": "question"},
        )
        assert response.status_code == 200
        job = registry.wait(response.json()["job"]["id"], timeout=2)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["answer"] == "backup answer"
    assert [call.rsplit("/", 1)[-1] for call in calls] == ["bad-model", "good-model"]
    assert probe.call_args.args[1].model == "bad-model"
    status = json.loads((kb_dir / ".openkb" / "model-pool" / "status.json").read_text(encoding="utf-8"))
    assert status["routes"]["primary:bad-model"]["health"] == "offline"
    assert status["routes"]["backup:good-model"]["health"] == "healthy"


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
