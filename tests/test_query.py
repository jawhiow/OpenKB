"""Tests for openkb.agent.query (Task 11)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents.tool import ToolContext

from openkb.agent.query import QueryReferenceTracker, build_query_agent, run_query
from openkb.schema import SCHEMA_MD


class TestBuildQueryAgent:
    def test_agent_name(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert agent.name == "wiki-query"

    def test_agent_has_adaptive_long_document_tool(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert len(agent.tools) == 4

    def test_agent_tool_names(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        names = {t.name for t in agent.tools}
        assert "read_file" in names
        assert "search_long_documents" in names
        assert "get_page_content" in names
        assert "get_image" in names

    def test_instructions_mention_adaptive_long_document_search(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "search_long_documents" in agent.instructions
        assert "get_page_content" in agent.instructions
        assert "pageindex_retrieve" not in agent.instructions

    def test_instructions_cover_local_long_docs(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "local-long" in agent.instructions
        assert "get_page_content(doc_name, pages)" in agent.instructions

    def test_instructions_mention_evidence_map(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "evidence_map.json" in agent.instructions

    def test_instructions_read_company_pages(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "Read company pages (companies/)" in agent.instructions

    def test_instructions_read_active_investment_schema_pages(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "industries/" in agent.instructions
        assert "themes/" not in agent.instructions
        assert "metrics/" not in agent.instructions
        assert "risks/" not in agent.instructions
        assert "including reusable themes, metrics, risks" in agent.instructions

    def test_schema_in_instructions(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert SCHEMA_MD in agent.instructions

    def test_agent_model(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENKB_WIRE_API", raising=False)
        monkeypatch.delenv("OPENAI_WIRE_API", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        agent = build_query_agent(str(tmp_path), "my-model")
        assert agent.model == "litellm/my-model"

    def test_agent_model_with_responses_api(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENKB_WIRE_API", "responses")
        agent = build_query_agent(str(tmp_path), "my-model")
        assert agent.model == "my-model"

    @pytest.mark.asyncio
    async def test_reference_tracker_records_tool_reads(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "sources" / "images" / "paper").mkdir(parents=True)
        (wiki / "AGENTS.md").write_text("Schema", encoding="utf-8")
        (wiki / "index.md").write_text("# Index", encoding="utf-8")
        (wiki / "sources" / "paper.json").write_text(
            '[{"page": 2, "content": "Page two"}]',
            encoding="utf-8",
        )
        (wiki / "summaries").mkdir()
        (wiki / "summaries" / "paper.md").write_text(
            "---\n"
            "doc_type: pageindex\n"
            "full_text: sources/paper.json\n"
            "---\n\n"
            "# Findings (pages 2-2)\n\n"
            "Summary: Page two discusses HBM demand.\n",
            encoding="utf-8",
        )
        (wiki / "sources" / "images" / "paper" / "p2.png").write_bytes(b"png")
        tracker = QueryReferenceTracker()
        agent = build_query_agent(str(wiki), "gpt-4o-mini", reference_tracker=tracker)
        tools = {tool.name: tool for tool in agent.tools}
        ctx = ToolContext(None, tool_name="test", tool_call_id="call_1", tool_arguments="{}")

        await tools["read_file"].on_invoke_tool(ctx, '{"path":"index.md"}')
        await tools["search_long_documents"].on_invoke_tool(
            ctx,
            '{"query":"HBM demand","doc_name":"paper","top_k":1}',
        )
        await tools["get_page_content"].on_invoke_tool(
            ctx,
            '{"doc_name":"paper","pages":"2"}',
        )
        await tools["get_image"].on_invoke_tool(
            ctx,
            '{"image_path":"sources/images/paper/p2.png"}',
        )

        assert tracker.references() == [
            {"type": "wiki_file", "path": "index.md"},
            {
                "type": "long_document_search",
                "query": "HBM demand",
                "doc_name": "paper",
                "top_k": 1,
            },
            {
                "type": "source_pages",
                "path": "sources/paper.json",
                "doc_name": "paper",
                "pages": "2",
            },
            {"type": "image", "path": "sources/images/paper/p2.png"},
        ]


class TestRunQuery:
    @pytest.mark.asyncio
    async def test_run_query_returns_final_output(self, tmp_path):
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".openkb").mkdir()

        mock_result = MagicMock()
        mock_result.final_output = "The answer is 42."

        with patch("openkb.agent.query.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            answer = await run_query("What is the answer?", tmp_path, "gpt-4o-mini")

        assert answer == "The answer is 42."

    @pytest.mark.asyncio
    async def test_run_query_passes_question_to_agent(self, tmp_path):
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".openkb").mkdir()

        captured = {}

        async def fake_run(agent, message, **kwargs):
            captured["message"] = message
            return MagicMock(final_output="answer")

        with patch("openkb.agent.query.Runner.run", side_effect=fake_run):
            await run_query("How does attention work?", tmp_path, "gpt-4o-mini")

        assert "How does attention work?" in captured["message"]

    @pytest.mark.asyncio
    async def test_run_query_uses_model_pool_routes_and_records_health(self, tmp_path):
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".openkb").mkdir()
        (tmp_path / ".openkb" / "hashes.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".openkb" / "config.yaml").write_text(
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
            "    api_key_env: OPENKB_LLM_PROFILE_PRIMARY_API_KEY\n"
            "    models:\n"
            "      - name: bad-model\n"
            "        weight: 100\n"
            "  - id: backup\n"
            "    name: Backup\n"
            "    model: good-model\n"
            "    wire_api: chat_completions\n"
            "    base_url: https://good.example.com/v1\n"
            "    api_key_env: OPENKB_LLM_PROFILE_BACKUP_API_KEY\n"
            "    models:\n"
            "      - name: good-model\n"
            "        weight: 100\n"
            "active_llm_profile: primary\n",
            encoding="utf-8",
        )
        from openkb.model_pool import record_route_success

        record_route_success(tmp_path, "primary", "bad-model", latency_ms=10)
        record_route_success(tmp_path, "backup", "good-model", latency_ms=20)
        calls: list[str] = []
        setup_profiles: list[dict[str, str]] = []

        async def fake_run(agent, message, **kwargs):
            calls.append(agent.model)
            if "bad-model" in agent.model:
                raise RuntimeError("upstream 500")
            return MagicMock(final_output="answer")

        def fake_setup(_kb_dir, profile=None):
            if profile is not None:
                setup_profiles.append(profile)

        with (
            patch("openkb.cli._setup_llm_key", side_effect=fake_setup),
            patch("openkb.model_pool.probe_model_route", side_effect=RuntimeError("probe failed")) as probe,
            patch("openkb.agent.query.Runner.run", side_effect=fake_run),
        ):
            answer = await run_query("How does attention work?", tmp_path, "fallback-model")

        assert answer == "answer"
        assert [call.rsplit("/", 1)[-1] for call in calls] == ["bad-model", "good-model"]
        assert probe.call_args.args[1].model == "bad-model"
        assert setup_profiles[-2]["id"] == "primary"
        assert setup_profiles[-1]["id"] == "backup"
