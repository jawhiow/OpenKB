from __future__ import annotations

from importlib import import_module
import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from agents import RawResponsesStreamEvent, RunItemStreamEvent
from openai.types.responses import ResponseTextDeltaEvent
import yaml

from openkb.client.jobs import JobRegistry
from openkb.client.server import create_app


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


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
        job = registry.wait(job_id, timeout=10)

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
    tracked = set(_git(kb_dir, "ls-files").splitlines())
    assert session_path.relative_to(kb_dir).as_posix() in tracked
    assert not any(path.startswith("raw/") for path in tracked)
    assert _git(kb_dir, "log", "-1", "--pretty=%s") == "Query first question"
    assert mock_run.call_args.args[1] == [
        {"role": "user", "content": "first question"}
    ]


def test_create_chat_endpoint_persists_empty_session(tmp_path):
    kb_dir = _make_kb(tmp_path)
    client = TestClient(create_app())

    response = client.post("/api/chats", json={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    session = response.json()
    assert session["id"]
    assert session["turn_count"] == 0
    assert session["history"] == []
    session_path = kb_dir / ".openkb" / "chats" / f"{session['id']}.json"
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["id"] == session["id"]
    listed = client.get("/api/chats", params={"kb_dir": str(kb_dir)}).json()["sessions"]
    assert listed[0]["id"] == session["id"]
    assert _git(kb_dir, "log", "-1", "--pretty=%s") == f"Create chat {session['id']}"


def test_save_chat_exploration_endpoint_exports_existing_session(tmp_path):
    kb_dir = _make_kb(tmp_path)
    client = TestClient(create_app())
    from openkb.agent.chat_session import ChatSession

    session = ChatSession.new(kb_dir, "gpt-5.4-mini", "zh")
    session.record_turn(
        "后续是否值得保存？",
        "这段对话可以作为探索记录。",
        [
            {"role": "user", "content": "后续是否值得保存？"},
            {"role": "assistant", "content": "这段对话可以作为探索记录。"},
        ],
    )

    response = client.post(
        f"/api/chats/{session.id}/save-exploration",
        json={"kb_dir": str(kb_dir), "name": "事后保存测试"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session.id
    assert body["path"].startswith("explorations/")
    exported = kb_dir / "wiki" / body["path"]
    text = exported.read_text(encoding="utf-8")
    assert f'session: "{session.id}"' in text
    assert "# Chat transcript" in text
    assert "## [1] 后续是否值得保存？" in text
    assert "这段对话可以作为探索记录。" in text
    assert _git(kb_dir, "log", "-1", "--pretty=%s") == f"Save chat transcript {exported.name}"


def test_save_chat_exploration_rejects_empty_session(tmp_path):
    kb_dir = _make_kb(tmp_path)
    client = TestClient(create_app())
    from openkb.agent.chat_session import ChatSession

    session = ChatSession.new(kb_dir, "gpt-5.4-mini", "zh")
    session.save()

    response = client.post(
        f"/api/chats/{session.id}/save-exploration",
        json={"kb_dir": str(kb_dir)},
    )

    assert response.status_code == 400
    assert "empty chat session" in response.json()["detail"]


def test_wiki_file_put_saves_content(tmp_path):
    kb_dir = _make_kb(tmp_path)
    page = kb_dir / "wiki" / "concepts" / "edit-me.md"
    page.write_text("# Before\n", encoding="utf-8")
    client = TestClient(create_app())

    response = client.put(
        "/api/wiki/file",
        json={
            "kb_dir": str(kb_dir),
            "path": "concepts/edit-me.md",
            "content": "# After\n\nUpdated body.\n",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"path": "concepts/edit-me.md"}
    assert page.read_text(encoding="utf-8") == "# After\n\nUpdated body.\n"


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


def test_llm_usage_retains_only_latest_200_rows(tmp_path):
    kb_dir = _make_kb(tmp_path)
    usage = import_module("openkb.llm_usage")

    for index in range(205):
        usage.record_usage(
            kb_dir=kb_dir,
            feature="query",
            model=f"model-{index}",
            wire_api="responses",
            base_url="",
            status="succeeded",
            duration_ms=index,
            error="",
            input_payload={"index": index},
            output_payload={"text": f"row-{index}"},
            created_at=f"2026-05-06T10:{index // 60:02d}:{index % 60:02d}Z",
        )

    rows = usage.export_usage(kb_dir)
    assert len(rows) == 200
    assert rows[0]["model"] == "model-204"
    assert rows[-1]["model"] == "model-5"
    listed = usage.list_usage(kb_dir, page=1, page_size=200)
    assert listed["total"] == 200
    assert listed["items"][0]["model"] == "model-204"
    assert listed["items"][-1]["model"] == "model-5"


def test_client_root_reports_api_and_new_ui_metadata():
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "openkb-client-api",
        "ui": "openkb-new-ui",
        "ui_dev_url": "http://127.0.0.1:8764",
    }


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
        first_job = registry.wait(first_response.json()["job"]["id"], timeout=10)
        response = client.post(
            "/api/query",
            json={
                "kb_dir": str(kb_dir),
                "question": "follow up",
                "session_id": first_job.result["session_id"],
            },
        )
        assert response.status_code == 200
        job = registry.wait(response.json()["job"]["id"], timeout=10)

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
            yield RunItemStreamEvent(
                name="tool_called",
                item=SimpleNamespace(
                    type="tool_call_item",
                    raw_item=SimpleNamespace(name="read_file", arguments='{"path":"index.md"}'),
                ),
            )
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
    assert 'event: status\ndata: {"message":"Running query..."}' in body
    assert 'event: status\ndata: {"message":"Reading wiki context..."}' in body
    assert 'event: delta\ndata: {"text":"Hello"}' in body
    assert 'event: delta\ndata: {"text":" world"}' in body
    done_payload = json.loads(body.split("event: done\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert done_payload["answer"] == "Hello world"
    assert done_payload["references"] == [{"type": "wiki_file", "path": "index.md"}]
    assert done_payload["session"]["turn_count"] == 1
    assert done_payload["session_id"] == done_payload["session"]["id"]


def test_query_stream_uses_model_pool_and_retries_after_runtime_failure(tmp_path):
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
    client = TestClient(create_app())
    calls: list[str] = []

    class _BadStreamResult:
        final_output = ""

        async def stream_events(self):
            raise RuntimeError("upstream 500")
            yield  # pragma: no cover

        def to_input_list(self):
            return []

    class _GoodStreamResult:
        final_output = "backup answer"

        async def stream_events(self):
            yield RawResponsesStreamEvent(
                data=ResponseTextDeltaEvent(
                    content_index=0,
                    delta="backup answer",
                    item_id="item_1",
                    logprobs=[],
                    output_index=0,
                    sequence_number=1,
                    type="response.output_text.delta",
                )
            )

        def to_input_list(self):
            return [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "backup answer"},
            ]

    def fake_run_streamed(agent, message, **kwargs):
        calls.append(agent.model)
        if "bad-model" in agent.model:
            return _BadStreamResult()
        return _GoodStreamResult()

    with (
        patch("openkb.cli._setup_llm_key"),
        patch("openkb.agent.query.Runner.run_streamed", side_effect=fake_run_streamed),
    ):
        response = client.post(
            "/api/query/stream",
            json={"kb_dir": str(kb_dir), "question": "question"},
        )

    assert response.status_code == 200
    body = response.text
    assert 'event: done\ndata: {"answer":"backup answer"' in body
    assert [call.rsplit("/", 1)[-1] for call in calls] == ["bad-model", "good-model"]
    status = json.loads((kb_dir / ".openkb" / "model-pool" / "status.json").read_text(encoding="utf-8"))
    assert status["routes"]["primary:bad-model"]["health"] == "offline"
    assert status["routes"]["backup:good-model"]["health"] == "healthy"


def test_query_stream_uses_active_profile_runtime(tmp_path):
    kb_dir = _make_kb(tmp_path)
    config_path = kb_dir / ".openkb" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "active_llm_profile": "deepseek",
            "llm_profiles": [
                {
                    "id": "deepseek",
                    "model": "openai/deepseek-v3",
                    "wire_api": "chat_completions",
                    "base_url": "https://llm.example.test/v1",
                    "provider": "deepseek",
                    "reasoning_effort": "high",
                    "thinking_enabled": True,
                    "api_key_env": "OPENKB_TEST_PROFILE_KEY",
                    "enabled": True,
                }
            ],
        }
    )
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    (kb_dir / ".env").write_text("OPENKB_TEST_PROFILE_KEY=sk-profile\n", encoding="utf-8")
    client = TestClient(create_app())
    calls: dict[str, object] = {}

    class FakeStreamResult:
        final_output = "profile answer"

        async def stream_events(self):
            yield RawResponsesStreamEvent(
                data=ResponseTextDeltaEvent(
                    content_index=0,
                    delta="profile answer",
                    item_id="item_1",
                    logprobs=[],
                    output_index=0,
                    sequence_number=1,
                    type="response.output_text.delta",
                )
            )

        def to_input_list(self):
            return [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "profile answer"},
            ]

    def fake_run_streamed(agent, message, **kwargs):
        from openkb.llm_runtime import get_api_key, get_base_url, get_model_provider, get_reasoning_effort, get_wire_api
        from openkb.llm_usage import get_llm_usage_context

        calls["model"] = agent.model
        calls["api_key"] = get_api_key()
        calls["base_url"] = get_base_url("openai/deepseek-v3")
        calls["wire_api"] = get_wire_api("openai/deepseek-v3")
        calls["provider"] = get_model_provider()
        calls["reasoning_effort"] = get_reasoning_effort()
        calls["thinking_enabled"] = os.environ.get("OPENKB_DEEPSEEK_THINKING_ENABLED")
        calls["usage_feature"] = get_llm_usage_context().feature if get_llm_usage_context() else None
        return FakeStreamResult()

    with patch("openkb.agent.query.Runner.run_streamed", side_effect=fake_run_streamed):
        response = client.post(
            "/api/query/stream",
            json={"kb_dir": str(kb_dir), "question": "question"},
        )

    assert response.status_code == 200
    assert 'event: done\ndata: {"answer":"profile answer"' in response.text
    assert calls == {
        "model": "litellm/custom_openai/deepseek-v3",
        "api_key": "sk-profile",
        "base_url": "https://llm.example.test/v1",
        "wire_api": "chat_completions",
        "provider": "deepseek",
        "reasoning_effort": "high",
        "thinking_enabled": "true",
        "usage_feature": "query",
    }


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
    job = registry.wait(job_id, timeout=10)

    assert job is not None
    assert job.status == "failed"
    assert job.error == "llm timeout"
    assert calls["file_path"] == source
    assert calls["target_kb"] == kb_dir
    assert calls["strict"] is True
    assert any(entry["message"] == "Compiling short document: doc.txt" for entry in job.logs)
    assert job.progress_current == 0
    assert job.progress_total == 1


def test_import_document_job_uses_source_only_pipeline(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    source = tmp_path / "doc.txt"
    source.write_text("hello", encoding="utf-8")
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_import_document_source(file_path, target_kb, *, force=False, strategy_override=None, job=None):
        calls["file_path"] = file_path
        calls["target_kb"] = target_kb
        calls["force"] = force
        calls["strategy_override"] = strategy_override
        return {"name": file_path.name, "file_hash": "hash-a", "skipped": False}

    monkeypatch.setattr("openkb.workflows.import_pipeline.import_document_source", fake_import_document_source)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", lambda *_args, **_kwargs: None)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/import")
    response = route.endpoint({"kb_dir": str(kb_dir), "path": str(source), "force": True})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert job.type == "import"
    assert job.result["imported"] == 1
    assert job.result["total"] == 1
    assert calls == {
        "file_path": source,
        "target_kb": kb_dir,
        "force": True,
        "strategy_override": None,
    }


def test_retry_import_document_uses_failed_inventory_raw_path(tmp_path, monkeypatch):
    from openkb.document_ledger import upsert_document_ledger_record

    kb_dir = _make_kb(tmp_path)
    raw = kb_dir / ".openkb" / "raw" / "2026-05-14" / "broken.txt"
    raw.parent.mkdir(parents=True)
    raw.write_text("retry me", encoding="utf-8")
    upsert_document_ledger_record(
        kb_dir,
        "hash-broken",
        {
            "name": "broken.txt",
            "stem": "broken",
            "raw_path": ".openkb/raw/2026-05-14/broken.txt",
            "workflow_state": {"source_state": "failed"},
            "execution": {"last_error": "conversion exploded", "retry_count": 1},
        },
    )
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_import_document_source(file_path, target_kb, *, force=False, strategy_override=None, job=None):
        calls["file_path"] = file_path
        calls["target_kb"] = target_kb
        calls["force"] = force
        calls["strategy_override"] = strategy_override
        return {"name": file_path.name, "file_hash": "hash-broken", "skipped": False}

    monkeypatch.setattr("openkb.workflows.import_pipeline.import_document_source", fake_import_document_source)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", lambda *_args, **_kwargs: None)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        "/api/documents/hash-broken/retry-import",
        json={"kb_dir": str(kb_dir)},
    )

    assert response.status_code == 200
    job = registry.wait(response.json()["job"]["id"], timeout=10)
    assert job is not None
    assert job.status == "succeeded"
    assert job.type == "import"
    assert calls == {
        "file_path": raw.resolve(),
        "target_kb": kb_dir,
        "force": True,
        "strategy_override": None,
    }


def test_summarize_document_job_uses_summary_pipeline(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("LLM_API_KEY=sk-test\n", encoding="utf-8")
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_summarize_documents(target_kb, *, file_hashes=None, model=None, force=False):
        from openkb.llm_runtime import get_api_key, get_base_url, get_wire_api
        from openkb.llm_usage import get_llm_usage_context

        calls["target_kb"] = target_kb
        calls["file_hashes"] = file_hashes
        calls["model"] = model
        calls["force"] = force
        calls["api_key"] = get_api_key()
        calls["base_url"] = get_base_url(model)
        calls["wire_api"] = get_wire_api(model)
        calls["usage_feature"] = get_llm_usage_context().feature if get_llm_usage_context() else None
        return {"generated": 1, "skipped": 0, "failed": 0, "total": 1, "failures": [], "documents": []}

    monkeypatch.setattr("openkb.workflows.summary_pipeline.summarize_documents", fake_summarize_documents)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", lambda *_args, **_kwargs: None)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/summarize")
    response = route.endpoint({"kb_dir": str(kb_dir), "file_hashes": ["hash-a"], "model": "gpt-test", "force": True})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert job.type == "summarize"
    assert job.result["generated"] == 1
    assert calls == {
        "target_kb": kb_dir,
        "file_hashes": ["hash-a"],
        "model": "gpt-test",
        "force": True,
        "api_key": "sk-test",
        "base_url": None,
        "wire_api": "responses",
        "usage_feature": "summary",
    }


def test_summarize_document_job_uses_active_profile_runtime(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    config_path = kb_dir / ".openkb" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "active_llm_profile": "deepseek",
            "llm_profiles": [
                {
                    "id": "deepseek",
                    "model": "openai/deepseek-v3",
                    "wire_api": "chat_completions",
                    "base_url": "https://llm.example.test/v1",
                    "api_key_env": "OPENKB_TEST_PROFILE_KEY",
                    "enabled": True,
                }
            ],
        }
    )
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    (kb_dir / ".env").write_text("OPENKB_TEST_PROFILE_KEY=sk-profile\n", encoding="utf-8")
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_summarize_documents(target_kb, *, file_hashes=None, model=None, force=False):
        from openkb.llm_runtime import get_api_key, get_base_url, get_wire_api
        from openkb.llm_usage import get_llm_usage_context

        calls["target_kb"] = target_kb
        calls["model"] = model
        calls["api_key"] = get_api_key()
        calls["base_url"] = get_base_url(model)
        calls["wire_api"] = get_wire_api(model)
        calls["usage_feature"] = get_llm_usage_context().feature if get_llm_usage_context() else None
        return {"generated": 0, "skipped": 1, "failed": 0, "total": 1, "failures": [], "documents": []}

    monkeypatch.setattr("openkb.workflows.summary_pipeline.summarize_documents", fake_summarize_documents)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", lambda *_args, **_kwargs: None)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/summarize")
    response = route.endpoint({"kb_dir": str(kb_dir), "file_hashes": ["hash-a"]})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert calls == {
        "target_kb": kb_dir,
        "model": "openai/deepseek-v3",
        "api_key": "sk-profile",
        "base_url": "https://llm.example.test/v1",
        "wire_api": "chat_completions",
        "usage_feature": "summary",
    }


def test_summarize_document_job_parallelizes_across_model_pool_routes(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("P1_KEY=sk-p1\nP2_KEY=sk-p2\n", encoding="utf-8")
    registry = JobRegistry()
    started = threading.Barrier(2)
    seen: list[tuple[tuple[str, ...], str, str, str | None]] = []
    seen_lock = threading.Lock()

    routes = [
        SimpleNamespace(
            profile_id="p1",
            profile_name="primary",
            model="model-a",
            wire_api="chat_completions",
            base_url="https://a.example/v1",
            api_key_env="P1_KEY",
            provider="generic",
            reasoning_effort="",
            thinking_enabled=False,
        ),
        SimpleNamespace(
            profile_id="p2",
            profile_name="backup",
            model="model-b",
            wire_api="chat_completions",
            base_url="https://b.example/v1",
            api_key_env="P2_KEY",
            provider="generic",
            reasoning_effort="",
            thinking_enabled=False,
        ),
    ]

    def fake_summarize_documents(target_kb, *, file_hashes=None, model=None, force=False, max_workers=1, progress_callback=None):
        from openkb.llm_runtime import get_api_key, get_base_url

        assert target_kb == kb_dir
        assert max_workers == 1
        assert file_hashes
        if progress_callback:
            progress_callback({"event": "start", "index": 1, "file_hash": file_hashes[0], "completed": 0, "total": len(file_hashes)})
        started.wait(timeout=2)
        with seen_lock:
            seen.append((tuple(file_hashes), model, get_api_key(), get_base_url(model)))
        if progress_callback:
            progress_callback({
                "event": "generated",
                "index": 1,
                "file_hash": file_hashes[0],
                "name": f"{file_hashes[0]}.md",
                "summary_path": f"review_summaries/{file_hashes[0]}.md",
                "completed": 1,
                "total": len(file_hashes),
            })
        return {
            "generated": len(file_hashes),
            "skipped": 0,
            "failed": 0,
            "total": len(file_hashes),
            "failures": [],
            "documents": [{"file_hash": item, "name": f"{item}.md"} for item in file_hashes],
        }

    monkeypatch.setattr("openkb.cli._healthy_model_pool_routes", lambda _kb: routes)
    monkeypatch.setattr("openkb.workflows.summary_pipeline._selected_summary_hashes", lambda _kb, *, file_hashes=None: ["hash-a", "hash-b"])
    monkeypatch.setattr("openkb.workflows.summary_pipeline.summarize_documents", fake_summarize_documents)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", lambda *_args, **_kwargs: None)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/summarize")
    response = route.endpoint({"kb_dir": str(kb_dir), "file_hashes": ["hash-a", "hash-b"]})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded", job.error
    assert job.result["generated"] == 2
    assert job.progress_current == 2
    assert job.progress_total == 2
    normalized = sorted((hashes, model, api_key, base_url) for hashes, model, api_key, base_url in seen)
    assert normalized == [
        (("hash-a",), "model-a", "sk-p1", "https://a.example/v1"),
        (("hash-b",), "model-b", "sk-p2", "https://b.example/v1"),
    ]
    assert any("Summarizing with 2 parallel task(s)" in entry["message"] for entry in job.logs)
    assert any("Summarized" in entry["message"] for entry in job.logs)


def test_review_summary_job_uses_summary_review_pipeline(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    reviews = [{"file_hash": "hash-a", "review_state": "approved", "summary_score": 90}]
    calls: dict[str, object] = {}

    def fake_update_summary_reviews(target_kb, review_payload):
        calls["target_kb"] = target_kb
        calls["reviews"] = review_payload
        return {"updated": 1, "failed": 0, "total": 1, "failures": [], "documents": []}

    monkeypatch.setattr("openkb.workflows.summary_pipeline.update_summary_reviews", fake_update_summary_reviews)
    monkeypatch.setattr("openkb.client.server.commit_kb_paths", lambda *_args, **_kwargs: None)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/review-summary")
    response = route.endpoint({"kb_dir": str(kb_dir), "reviews": reviews})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert job.type == "review_summary"
    assert job.result["updated"] == 1
    assert calls == {"target_kb": kb_dir, "reviews": reviews}


def test_review_summary_job_commits_only_document_ledger(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    reviews = [{"file_hash": "hash-a", "review_state": "approved", "summary_score": 90}]
    commit_calls: list[tuple[Path, str, tuple[str, ...]]] = []

    def fake_update_summary_reviews(_target_kb, _review_payload):
        return {"updated": 1, "failed": 0, "total": 1, "failures": [], "documents": []}

    def fail_full_commit(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git", "add", "-A", "--", "."])

    def fake_commit_kb_paths(target_kb, message, paths):
        commit_calls.append((target_kb, message, tuple(paths)))

    monkeypatch.setattr("openkb.workflows.summary_pipeline.update_summary_reviews", fake_update_summary_reviews)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", fail_full_commit)
    monkeypatch.setattr("openkb.client.server.commit_kb_paths", fake_commit_kb_paths)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/review-summary")
    response = route.endpoint({"kb_dir": str(kb_dir), "reviews": reviews})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded", job.error
    assert job.result["updated"] == 1
    assert commit_calls == [(kb_dir, "Review 1 summary document(s)", (".openkb/document_ledger.json",))]


def test_review_summary_job_succeeds_when_git_autocommit_fails(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    registry = JobRegistry()
    reviews = [{"file_hash": "hash-a", "review_state": "approved", "summary_score": 90}]

    def fake_update_summary_reviews(_target_kb, _review_payload):
        return {"updated": 1, "failed": 0, "total": 1, "failures": [], "documents": []}

    def fail_commit(_target_kb, _message, _paths):
        raise subprocess.CalledProcessError(
            128,
            ["git", "add", "--", ".gitignore"],
            stderr="fatal: Unable to create '.git/index.lock': File exists.",
        )

    monkeypatch.setattr("openkb.workflows.summary_pipeline.update_summary_reviews", fake_update_summary_reviews)
    monkeypatch.setattr("openkb.client.server.commit_kb_paths", fail_commit)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/review-summary")
    response = route.endpoint({"kb_dir": str(kb_dir), "reviews": reviews})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded", job.error
    assert job.result["updated"] == 1
    assert any(entry["level"] == "warning" and "Git auto-commit failed" in entry["message"] for entry in job.logs)


def test_promote_document_job_uses_promotion_pipeline(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("LLM_API_KEY=sk-test\n", encoding="utf-8")
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_promote_summary_documents(target_kb, *, file_hashes=None, model=None, force=False, progress_callback=None):
        from openkb.llm_usage import get_llm_usage_context

        calls["target_kb"] = target_kb
        calls["file_hashes"] = file_hashes
        calls["model"] = model
        calls["force"] = force
        calls["usage_feature"] = get_llm_usage_context().feature if get_llm_usage_context() else None
        if progress_callback:
            progress_callback({"event": "selected", "completed": 0, "total": 1})
            progress_callback({"event": "start", "index": 1, "file_hash": "hash-a", "completed": 0, "total": 1})
            progress_callback(
                {
                    "event": "promoted",
                    "index": 1,
                    "file_hash": "hash-a",
                    "name": "report.md",
                    "summary_path": "summaries/report.md",
                    "source_path": "sources/report.md",
                    "completed": 1,
                    "total": 1,
                }
            )
        return {
            "promoted": 1,
            "skipped": 0,
            "failed": 0,
            "total": 1,
            "failures": [],
            "documents": [
                {
                    "file_hash": "hash-a",
                    "name": "report.md",
                    "skipped": False,
                    "promotion_state": "promoted",
                    "summary_path": "summaries/report.md",
                    "source_path": "sources/report.md",
                    "model": "gpt-test",
                }
            ],
        }

    monkeypatch.setattr("openkb.workflows.promotion_pipeline.promote_summary_documents", fake_promote_summary_documents)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", lambda *_args, **_kwargs: None)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/promote")
    response = route.endpoint({"kb_dir": str(kb_dir), "file_hashes": ["hash-a"], "model": "gpt-test", "force": True})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert job.type == "promote"
    assert job.result["promoted"] == 1
    assert job.result["documents"][0]["summary_path"] == "summaries/report.md"
    assert job.progress_current == 1
    assert job.progress_total == 1
    assert any("Promoting 1: hash-a" in entry["message"] for entry in job.logs)
    assert any("Promoted 1: report.md -> summaries/report.md" in entry["message"] for entry in job.logs)
    assert calls == {
        "target_kb": kb_dir,
        "file_hashes": ["hash-a"],
        "model": "gpt-test",
        "force": True,
        "usage_feature": "promotion",
    }


def test_promote_document_job_parallelizes_across_model_pool_routes(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("P1_KEY=sk-p1\nP2_KEY=sk-p2\n", encoding="utf-8")
    registry = JobRegistry()
    started = threading.Barrier(2)
    seen: list[tuple[tuple[str, ...], str, str, str | None]] = []
    seen_lock = threading.Lock()

    routes = [
        SimpleNamespace(
            profile_id="p1",
            profile_name="primary",
            model="model-a",
            wire_api="chat_completions",
            base_url="https://a.example/v1",
            api_key_env="P1_KEY",
            provider="generic",
            reasoning_effort="",
            thinking_enabled=False,
        ),
        SimpleNamespace(
            profile_id="p2",
            profile_name="backup",
            model="model-b",
            wire_api="chat_completions",
            base_url="https://b.example/v1",
            api_key_env="P2_KEY",
            provider="generic",
            reasoning_effort="",
            thinking_enabled=False,
        ),
    ]

    def fake_promote_summary_documents(
        target_kb,
        *,
        file_hashes=None,
        model=None,
        force=False,
        max_workers=1,
        progress_callback=None,
    ):
        from openkb.llm_runtime import get_api_key, get_base_url

        assert target_kb == kb_dir
        assert max_workers == 1
        assert file_hashes
        if progress_callback:
            progress_callback({"event": "start", "index": 1, "file_hash": file_hashes[0], "completed": 0, "total": len(file_hashes)})
        if len(file_hashes) == 1:
            started.wait(timeout=2)
        with seen_lock:
            seen.append((tuple(file_hashes), model, get_api_key(), get_base_url(model)))
        if progress_callback:
            progress_callback({
                "event": "promoted",
                "index": 1,
                "file_hash": file_hashes[0],
                "name": f"{file_hashes[0]}.md",
                "summary_path": f"summaries/{file_hashes[0]}.md",
                "source_path": f"sources/{file_hashes[0]}.md",
                "completed": 1,
                "total": len(file_hashes),
            })
        return {
            "promoted": len(file_hashes),
            "skipped": 0,
            "failed": 0,
            "total": len(file_hashes),
            "failures": [],
            "documents": [{"file_hash": item, "name": f"{item}.md"} for item in file_hashes],
        }

    monkeypatch.setattr("openkb.cli._healthy_model_pool_routes", lambda _kb: routes)
    monkeypatch.setattr("openkb.workflows.promotion_pipeline._selected_promotion_hashes", lambda _kb, *, file_hashes=None: ["hash-a", "hash-b"])
    monkeypatch.setattr("openkb.workflows.promotion_pipeline.promote_summary_documents", fake_promote_summary_documents)
    monkeypatch.setattr("openkb.client.server.commit_kb_changes", lambda *_args, **_kwargs: None)

    app = create_app(registry=registry)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/documents/promote")
    response = route.endpoint({"kb_dir": str(kb_dir), "file_hashes": ["hash-a", "hash-b"]})
    job = registry.wait(response["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded", job.error
    assert job.result["promoted"] == 2
    assert job.progress_current == 2
    assert job.progress_total == 2
    normalized = sorted((hashes, model, api_key, base_url) for hashes, model, api_key, base_url in seen)
    assert normalized == [
        (("hash-a",), "model-a", "sk-p1", "https://a.example/v1"),
        (("hash-b",), "model-b", "sk-p2", "https://b.example/v1"),
    ]
    assert any("Promoting with 2 parallel task(s)" in entry["message"] for entry in job.logs)
    assert any("Promoted" in entry["message"] for entry in job.logs)


def test_add_document_job_passes_ingest_gate_overrides(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    source = tmp_path / "doc.txt"
    source.write_text("hello", encoding="utf-8")
    registry = JobRegistry()
    calls: dict[str, object] = {}

    def fake_add_single_file(
        file_path,
        target_kb,
        *,
        strict=False,
        progress_callback=None,
        force_gate_pass=False,
        force_gate_reject=False,
        gate_reason="",
        gate_operator="",
    ):
        calls["force_gate_pass"] = force_gate_pass
        calls["force_gate_reject"] = force_gate_reject
        calls["gate_reason"] = gate_reason
        calls["gate_operator"] = gate_operator

    monkeypatch.setattr("openkb.cli.add_single_file", fake_add_single_file)

    client = TestClient(create_app(registry=registry))
    response = client.post(
        "/api/documents/add",
        json={
            "kb_dir": str(kb_dir),
            "path": str(source),
            "force_gate_pass": True,
            "gate_reason": "trusted primary filing",
            "gate_operator": "alice",
        },
    )

    assert response.status_code == 200
    job = registry.wait(response.json()["job"]["id"], timeout=10)
    assert job is not None
    assert job.status == "succeeded"
    assert calls == {
        "force_gate_pass": True,
        "force_gate_reject": False,
        "gate_reason": "trusted primary filing",
        "gate_operator": "alice",
    }


def test_ingest_gate_endpoint_returns_config_history_and_details(tmp_path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "language: zh\n"
        "ingest_gate:\n"
        "  enabled: true\n"
        "  pass_threshold: 82\n"
        "  hold_threshold: 66\n"
        "  hard_reject_enabled: true\n"
        "  log_all_decisions: true\n"
        "  allow_force_pass: true\n"
        "  allow_force_reject: false\n",
        encoding="utf-8",
    )
    history = kb_dir / ".openkb" / "ingest_gate_history.jsonl"
    history.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-10 10:00:00",
                        "doc_title": "old.pdf",
                        "final_decision": "REJECT",
                        "total_score": 40,
                        "dimension_scores": {"relevance": {"score": 4, "max": 15, "reason": "weak"}},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-11 10:00:00",
                        "doc_title": "new.pdf",
                        "final_decision": "PASS",
                        "total_score": 88,
                        "one_line_verdict": "high signal",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    client = TestClient(create_app(registry=JobRegistry()))
    response = client.get("/api/ingest-gate", params={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["config"]["enabled"] is True
    assert body["config"]["pass_threshold"] == 82
    assert body["config"]["hold_threshold"] == 66
    assert body["config"]["allow_force_reject"] is False
    assert body["log_page"] == "explorations/资料准入评分台账.md"
    assert body["summary"] == {
        "total": 2,
        "pass": 1,
        "hold": 0,
        "reject": 1,
        "force_pass": 0,
        "force_reject": 0,
        "average_score": 64.0,
        "latest_at": "2026-05-11 10:00:00",
    }
    assert [item["doc_title"] for item in body["decisions"]] == ["new.pdf", "old.pdf"]
    assert body["decisions"][0]["line_number"] == 2
    assert body["decisions"][0]["id"] == "2"


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
    job = registry.wait(job_id, timeout=10)

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
    job = registry.wait(job_id, timeout=10)

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
    job = registry.wait(job_id, timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert calls["file_path"].parent.parent == kb_dir / ".openkb" / "uploads"
    assert calls["file_path"].name == "doc.txt"
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
    job = registry.wait(job_id, timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert [path.parent.parent for path in calls] == [kb_dir / ".openkb" / "uploads", kb_dir / ".openkb" / "uploads"]
    assert {path.name for path in calls} == {"a.txt", "b.txt"}
    assert job.result["added"] == 2
    assert job.result["failed"] == 0
    assert job.result["total"] == 2


def test_upload_document_parallelizes_multiple_files_across_healthy_profiles(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    config = (kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8")
    (kb_dir / ".openkb" / "config.yaml").write_text(
        config
        + "model_pool:\n"
        + "  enabled: true\n"
        + "llm_profiles:\n"
        + "  - id: p1\n"
        + "    name: primary\n"
        + "    enabled: true\n"
        + "    model: model-a\n"
        + "    wire_api: responses\n"
        + "  - id: p2\n"
        + "    name: backup\n"
        + "    enabled: true\n"
        + "    model: model-b\n"
        + "    wire_api: responses\n",
        encoding="utf-8",
    )
    registry = JobRegistry()
    started = threading.Barrier(2)
    active_lock = threading.Lock()
    active = 0
    max_active = 0
    seen_routes: list[tuple[str, str, str]] = []

    def fake_routes(_kb_dir):
        assert _kb_dir == kb_dir
        return [
            SimpleNamespace(profile_id="p1", profile_name="primary", model="model-a"),
            SimpleNamespace(profile_id="p2", profile_name="backup", model="model-b"),
        ]

    def fake_add_single_file(file_path, target_kb, *, strict=False, progress_callback=None, model_route=None, job=None):
        nonlocal active, max_active
        assert target_kb == kb_dir
        assert strict is True
        assert model_route is not None
        assert job is not None
        if progress_callback:
            progress_callback(f"Compiling short document: {file_path.name}")
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            started.wait(timeout=2)
            seen_routes.append((file_path.name, model_route.profile_name, model_route.model))
        finally:
            with active_lock:
                active -= 1

    monkeypatch.setattr("openkb.cli._healthy_model_pool_routes", fake_routes)
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
    job = registry.wait(job_id, timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert max_active >= 2
    normalized = sorted((name.split("-", 3)[-1], profile, model) for name, profile, model in seen_routes)
    assert normalized == [
        ("a.txt", "primary", "model-a"),
        ("b.txt", "backup", "model-b"),
    ]
    assert any("Importing with 2 parallel task(s)" in entry["message"] for entry in job.logs)
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


def test_review_summary_file_endpoint_reads_review_staging_area(tmp_path):
    kb_dir = _make_kb(tmp_path)
    review_summary = kb_dir / ".openkb" / "review_summaries" / "2026-05-10" / "paper.md"
    review_summary.parent.mkdir(parents=True, exist_ok=True)
    review_summary.write_text("# Review Summary", encoding="utf-8")

    client = TestClient(create_app())
    response = client.get(
        "/api/review-summary/file",
        params={"kb_dir": str(kb_dir), "path": "review_summaries/2026-05-10/paper.md"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "review_summaries/2026-05-10/paper.md"
    assert body["content"] == "# Review Summary"


def test_raw_file_endpoint_serves_raw_source_inline(tmp_path):
    kb_dir = _make_kb(tmp_path)
    raw = kb_dir / "raw" / "paper.txt"
    raw.write_text("raw source body", encoding="utf-8")

    client = TestClient(create_app())
    response = client.get(
        "/api/raw/file",
        params={"kb_dir": str(kb_dir), "path": "raw/paper.txt"},
    )

    assert response.status_code == 200
    assert response.text == "raw source body"
    assert "inline" in response.headers["content-disposition"]


def test_raw_file_endpoint_rejects_path_escape(tmp_path):
    kb_dir = _make_kb(tmp_path)

    client = TestClient(create_app())
    response = client.get(
        "/api/raw/file",
        params={"kb_dir": str(kb_dir), "path": "raw/../wiki/index.md"},
    )

    assert response.status_code == 400


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
    job = registry.wait(job_id, timeout=10)

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
    for folder in ("companies", "industries", "explorations"):
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
        "5. **搭建 industries 目录** - 至少各创建 1-2 个种子页面\n"
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
    job = registry.wait(job_id, timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["created"] == [
        {"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "created"}
    ]
    ai_cpu = (kb_dir / "wiki" / "concepts" / "AI_CPU.md").read_text(encoding="utf-8")
    assert "status: draft" in ai_cpu
    assert "# AI CPU" in ai_cpu
    assert "TODO" not in ai_cpu
    assert (kb_dir / "wiki" / "concepts" / "AI_GPU.md").read_text(encoding="utf-8") == "# Existing AI GPU"
    assert not (kb_dir / "wiki" / "concepts" / "Cloud_CAPEX.md").exists()
    tracked = set(_git(kb_dir, "ls-files").splitlines())
    assert "wiki/concepts/AI_CPU.md" in tracked
    assert not any(path.startswith("raw/") for path in tracked)
    assert _git(kb_dir, "log", "-1", "--pretty=%s") == "Apply lint fixes"


def test_lint_apply_fixes_job_creates_approved_company_and_schema_drafts(tmp_path):
    kb_dir = _make_kb(tmp_path)
    for folder in ("companies", "industries"):
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
                    "reason": "搭建 industries 目录",
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
    job = registry.wait(job_id, timeout=10)

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
    job = registry.wait(job_id, timeout=10)

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
    job = registry.wait(job_id, timeout=10)

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
                        "probing": True,
                        "probe_source": "auto",
                        "last_probe_started_at": "2026-05-06T11:41:30Z",
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
    assert body["enabled"] is True
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
    assert card["probing"] is True
    assert card["probe_source"] == "auto"
    assert card["last_probe_started_at"] == "2026-05-06T11:41:30Z"
    assert card["available_models"] == ["gpt-4o-mini"]
    assert card["failed_models"] == {"gpt-5.4-mini": "model_not_found"}


def test_model_pool_endpoint_does_not_probe_on_page_read(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "language: zh\n"
        "wire_api: chat_completions\n"
        "model_pool:\n"
        "  enabled: true\n"
        "llm_profiles:\n"
        "  - id: gateway\n"
        "    name: Gateway\n"
        "    model: gpt-4o-mini\n"
        "    wire_api: chat_completions\n"
        "    base_url: https://gateway.example.com/v1\n"
        "    enabled: true\n"
        "active_llm_profile: gateway\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("openkb.model_pool.probe_model_route", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected probe")))
    client = TestClient(create_app())

    response = client.get("/api/model-pool", params={"kb_dir": str(kb_dir)})

    assert response.status_code == 200
    assert response.json()["profiles"][0]["health"] == "unknown"


def test_model_pool_probe_scheduler_probes_due_failed_enabled_profiles(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\n"
        "language: zh\n"
        "wire_api: chat_completions\n"
        "model_pool:\n"
        "  enabled: true\n"
        "  probe_interval_seconds: 1\n"
        "llm_profiles:\n"
        "  - id: gateway\n"
        "    name: Gateway\n"
        "    model: bad-model\n"
        "    wire_api: chat_completions\n"
        "    enabled: true\n"
        "active_llm_profile: gateway\n",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "model-pool").mkdir(parents=True)
    (kb_dir / ".openkb" / "model-pool" / "status.json").write_text(
        json.dumps(
            {
                "routes": {
                    "gateway:bad-model": {
                        "profile_id": "gateway",
                        "model": "bad-model",
                        "health": "offline",
                        "consecutive_failures": 1,
                        "last_checked_at": "2026-05-06T10:00:00Z",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_probe(_kb_dir, route, **_kwargs):
        calls.append(route.model)
        return {"ok": True}

    monkeypatch.setattr("openkb.model_pool.probe_model_route", fake_probe)
    from openkb.client.server import ModelPoolProbeScheduler

    results = ModelPoolProbeScheduler().run_once(kb_dir)

    assert calls == ["bad-model"]
    assert results[0]["probe_source"] == "auto"
    assert results[0]["health"] == "healthy"


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
    assert result["probing"] is False
    assert result["probe_source"] == "manual"
    assert result["last_probe_started_at"]
    assert result["available_models"] == ["gpt-4o-mini"]
    assert result["failed_models"] == {"missing-model": "model_not_found"}
    status = json.loads((kb_dir / ".openkb" / "model-pool" / "status.json").read_text(encoding="utf-8"))
    assert status["profiles"]["gateway"]["health"] == "degraded"
    assert status["profiles"]["gateway"]["probing"] is False
    assert status["profiles"]["gateway"]["probe_source"] == "manual"
    assert status["profiles"]["gateway"]["last_probe_started_at"]
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
        "slow-model",
        "fast-model",
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
    ):
        response = client.post(
            "/api/query",
            json={"kb_dir": str(kb_dir), "question": "question"},
        )
        assert response.status_code == 200
        job = registry.wait(response.json()["job"]["id"], timeout=10)

    assert job is not None
    assert job.status == "succeeded"
    assert job.result["answer"] == "backup answer"
    assert [call.rsplit("/", 1)[-1] for call in calls] == ["bad-model", "good-model"]
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
    assert current["ingest_gate_enabled"] is False
    assert current["ingest_gate_pass_threshold"] == 75
    assert current["ingest_gate_hold_threshold"] == 60
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
            "ingest_gate_enabled": True,
            "ingest_gate_pass_threshold": 82,
            "ingest_gate_hold_threshold": 65,
            "ingest_gate_hard_reject_enabled": False,
            "ingest_gate_log_all_decisions": False,
            "ingest_gate_allow_force_pass": False,
            "ingest_gate_allow_force_reject": False,
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
    assert updated["ingest_gate_enabled"] is True
    assert updated["ingest_gate_pass_threshold"] == 82
    assert updated["ingest_gate_hold_threshold"] == 65
    assert updated["ingest_gate_hard_reject_enabled"] is False
    assert updated["ingest_gate_log_all_decisions"] is False
    assert updated["ingest_gate_allow_force_pass"] is False
    assert updated["ingest_gate_allow_force_reject"] is False
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
            "ingest_gate_enabled": True,
            "ingest_gate_pass_threshold": 81,
            "ingest_gate_hold_threshold": 64,
            "ingest_gate_hard_reject_enabled": False,
            "ingest_gate_log_all_decisions": False,
            "ingest_gate_allow_force_pass": False,
            "ingest_gate_allow_force_reject": False,
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
            "model_pool_enabled": False,
            "model_pool_probe_interval_seconds": 300,
            "model_pool_failure_threshold": 2,
            "model_pool_timeout_seconds": 18,
            "api_key": "gateway-secret",
        },
    )
    assert create_response.status_code == 200
    profile_response = client.put(
        "/api/model-pool/profiles/gateway",
        json={
            "kb_dir": str(source),
            "name": "Gateway",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "models": [
                {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
                {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
            ],
            "enabled": False,
        },
    )
    assert profile_response.status_code == 200

    export_response = client.get("/api/config/export", params={"kb_dir": str(source)})

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported["format"] == "openkb.settings-config.v1"
    assert exported["settings"]["compile_max_concurrency"] == 3
    assert exported["settings"]["ingest_gate_enabled"] is True
    assert exported["settings"]["ingest_gate_pass_threshold"] == 81
    assert exported["settings"]["ingest_gate_hold_threshold"] == 64
    assert exported["settings"]["ingest_gate_hard_reject_enabled"] is False
    assert exported["settings"]["ingest_gate_log_all_decisions"] is False
    assert exported["settings"]["ingest_gate_allow_force_pass"] is False
    assert exported["settings"]["ingest_gate_allow_force_reject"] is False
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
    assert exported["settings"]["model_pool_enabled"] is False
    assert exported["settings"]["model_pool_strategy"] == "weighted_round_robin"
    assert exported["settings"]["model_pool_probe_interval_seconds"] == 300
    assert exported["settings"]["model_pool_failure_threshold"] == 2
    assert exported["settings"]["model_pool_timeout_seconds"] == 18
    assert exported["profiles"][0]["api_key"] == "default-secret"
    assert exported["profiles"][1]["api_key"] == "gateway-secret"
    assert exported["profiles"][1]["enabled"] is False
    assert exported["profiles"][1]["models"] == [
        {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
        {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
    ]

    import_response = client.post(
        "/api/config/import",
        json={"kb_dir": str(target), "config": exported},
    )

    assert import_response.status_code == 200
    imported = import_response.json()
    assert imported["active_profile"] == "gateway"
    assert imported["compile_max_concurrency"] == 3
    assert imported["ingest_gate_enabled"] is True
    assert imported["ingest_gate_pass_threshold"] == 81
    assert imported["ingest_gate_hold_threshold"] == 64
    assert imported["ingest_gate_hard_reject_enabled"] is False
    assert imported["ingest_gate_log_all_decisions"] is False
    assert imported["ingest_gate_allow_force_pass"] is False
    assert imported["ingest_gate_allow_force_reject"] is False
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
    assert imported["profiles"][1]["enabled"] is False
    assert imported["profiles"][1]["models"] == [
        {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
        {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
    ]
    saved = yaml.safe_load((target / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert saved["ingest_gate"]["enabled"] is True
    assert saved["ingest_gate"]["pass_threshold"] == 81
    assert saved["ingest_gate"]["hold_threshold"] == 64
    assert saved["llm_profiles"][1]["enabled"] is False
    assert saved["llm_profiles"][1]["models"] == [
        {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
        {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
    ]
    assert saved["model_pool"] == {
        "enabled": False,
        "failure_threshold": 2,
        "probe_interval_seconds": 300,
        "strategy": "weighted_round_robin",
        "timeout_seconds": 18,
    }
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


def test_test_llm_endpoint_applies_deepseek_profile_env(monkeypatch, tmp_path):
    kb_dir = _make_kb(tmp_path)
    captured = {}

    def fake_completion(model, messages, **kwargs):
        captured["model"] = model
        captured["provider"] = os.environ.get("OPENKB_MODEL_PROVIDER")
        captured["reasoning_effort"] = os.environ.get("OPENKB_MODEL_REASONING_EFFORT")
        captured["thinking_enabled"] = os.environ.get("OPENKB_DEEPSEEK_THINKING_ENABLED")
        return SimpleNamespace(text="pong")

    monkeypatch.setattr("openkb.llm_runtime.completion", fake_completion)

    client = TestClient(create_app())
    response = client.post(
        "/api/config/test-llm",
        json={
            "kb_dir": str(kb_dir),
            "model": "deepseek-v4-pro",
            "provider": "deepseek",
            "wire_api": "chat_completions",
            "base_url": "https://api.deepseek.com",
            "reasoning_effort": "high",
            "thinking_enabled": True,
            "api_key": "deepseek-secret",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "reasoning_effort": "high",
        "thinking_enabled": "true",
    }
