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

    def test_agent_has_three_tools(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert len(agent.tools) == 3

    def test_agent_tool_names(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        names = {t.name for t in agent.tools}
        assert "read_file" in names
        assert "get_page_content" in names
        assert "get_image" in names

    def test_instructions_mention_get_page_content(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
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

    def test_instructions_read_expanded_investment_schema_pages(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "industries/" in agent.instructions
        assert "themes/" in agent.instructions
        assert "metrics/" in agent.instructions
        assert "risks/" in agent.instructions

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
        (wiki / "sources" / "images" / "paper" / "p2.png").write_bytes(b"png")
        tracker = QueryReferenceTracker()
        agent = build_query_agent(str(wiki), "gpt-4o-mini", reference_tracker=tracker)
        tools = {tool.name: tool for tool in agent.tools}
        ctx = ToolContext(None, tool_name="test", tool_call_id="call_1", tool_arguments="{}")

        await tools["read_file"].on_invoke_tool(ctx, '{"path":"index.md"}')
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
