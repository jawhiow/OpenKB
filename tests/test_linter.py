"""Tests for openkb.agent.linter (Task 14)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.agent.linter import (
    apply_coverage_gap_concept_candidates,
    apply_lint_fix_candidates,
    build_lint_agent,
    extract_coverage_gap_concept_candidates,
    extract_lint_fix_candidates,
    format_coverage_gap_concept_candidates,
    run_knowledge_lint,
)
from openkb.schema import SCHEMA_MD


class TestBuildLintAgent:
    def test_agent_name(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert agent.name == "wiki-linter"

    def test_agent_has_two_tools(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert len(agent.tools) == 2

    def test_agent_tool_names(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        names = {t.name for t in agent.tools}
        assert "list_files" in names
        assert "read_file" in names

    def test_schema_in_instructions(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert SCHEMA_MD in agent.instructions

    def test_agent_model(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENKB_WIRE_API", raising=False)
        monkeypatch.delenv("OPENAI_WIRE_API", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        agent = build_lint_agent(str(tmp_path), "custom-model")
        assert agent.model == "litellm/custom-model"

    def test_agent_model_with_responses_api(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENKB_WIRE_API", "responses")
        agent = build_lint_agent(str(tmp_path), "custom-model")
        assert agent.model == "custom-model"

    def test_instructions_mention_contradictions(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert "Contradictions" in agent.instructions or "contradictions" in agent.instructions

    def test_instructions_mention_gaps(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert "Gaps" in agent.instructions or "gaps" in agent.instructions

    def test_instructions_audit_company_pages(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert "Company boundary" in agent.instructions
        assert "companies/" in agent.instructions

    def test_instructions_ignore_legacy_investment_schema_pages(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert "Ignore deprecated legacy directories" in agent.instructions


class TestCoverageGapCandidates:
    def test_extracts_missing_durable_concepts_from_gap_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "concepts" / "Cloud_CAPEX.md").write_text("# Cloud CAPEX", encoding="utf-8")
        report = (
            "## Gaps\n"
            "- Missing concept page for AI CPU and global AI GPU.\n"
            "- 未覆盖出口管制和非AI半导体景气周期。\n"
            "- Cloud CAPEX lacks context but already has a page.\n"
        )

        candidates = extract_coverage_gap_concept_candidates(wiki, report)

        assert [item["name"] for item in candidates] == [
            "AI_CPU",
            "AI_GPU",
            "Export_Controls",
            "Semiconductor_Cycle",
        ]
        assert candidates[0]["path"] == "concepts/AI_CPU.md"
        assert candidates[0]["action"] == "create"

    def test_format_lists_candidate_links(self):
        report = format_coverage_gap_concept_candidates(
            [{"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "create"}]
        )

        assert "Coverage Gap Candidates" in report
        assert "[[concepts/AI_CPU]]" in report
        assert "AI CPU" in report

    def test_apply_skips_coverage_drafts_without_source_evidence(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )

        created = apply_coverage_gap_concept_candidates(
            wiki,
            [
                {"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "create"},
                {"name": "AI_GPU", "title": "AI GPU", "path": "concepts/AI_GPU.md", "action": "create"},
            ],
        )

        assert created == []
        assert not (wiki / "concepts" / "AI_CPU.md").exists()
        assert not (wiki / "concepts" / "AI_GPU.md").exists()
        index = (wiki / "index.md").read_text(encoding="utf-8")
        assert "coverage-gap draft" not in index

    def test_apply_seeds_drafts_with_matching_summary_evidence(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "report.md").write_text(
            "---\ndoc_type: local-long\n---\n\n"
            "# Report\n\n"
            "## AI CPU and global AI GPU\n"
            "NVIDIA Grace CPU, GB300, and Rubin roadmap support AI accelerator demand.",
            encoding="utf-8",
        )

        apply_coverage_gap_concept_candidates(
            wiki,
            [{"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "create"}],
        )

        ai_cpu = (wiki / "concepts" / "AI_CPU.md").read_text(encoding="utf-8")
        assert "status: draft" in ai_cpu
        assert "# AI CPU" in ai_cpu
        assert "## Source Evidence" in ai_cpu
        assert "TODO" not in ai_cpu
        assert "sources: [summaries/report.md]" in ai_cpu
        assert "- [[summaries/report]]" in ai_cpu
        assert "NVIDIA Grace CPU" in ai_cpu
        index = (wiki / "index.md").read_text(encoding="utf-8")
        assert "- [[concepts/AI_CPU]] - AI CPU (coverage-gap draft)" in index

    def test_apply_prefers_page_level_evidence_for_local_long_sources(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries" / "report.md").write_text(
            "---\n"
            "doc_type: local-long\n"
            "full_text: sources/report.json\n"
            "---\n\n"
            "# Report\n\n"
            "## AI CPU and global AI GPU\n"
            "The report discusses AI CPU roadmaps.",
            encoding="utf-8",
        )
        (wiki / "sources" / "report.json").write_text(
            '[{"page": 1, "content": "Background only."}, '
            '{"page": 2, "content": "NVIDIA Grace CPU and Rubin roadmap drive AI accelerator demand."}]',
            encoding="utf-8",
        )

        apply_coverage_gap_concept_candidates(
            wiki,
            [{"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "create"}],
        )

        ai_cpu = (wiki / "concepts" / "AI_CPU.md").read_text(encoding="utf-8")
        assert "- [[summaries/report]] p.2:" in ai_cpu
        assert "NVIDIA Grace CPU" in ai_cpu

    def test_apply_writes_structured_evidence_map(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries" / "report.md").write_text(
            "---\nfull_text: sources/report.json\n---\n\n"
            "## AI CPU\nThe report discusses AI CPU roadmaps.",
            encoding="utf-8",
        )
        (wiki / "sources" / "report.json").write_text(
            json.dumps([
                {"page": 2, "content": "NVIDIA Grace CPU supports AI accelerator demand."},
            ]),
            encoding="utf-8",
        )

        apply_coverage_gap_concept_candidates(
            wiki,
            [{"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "create"}],
        )

        evidence = json.loads((wiki / "evidence_map.json").read_text(encoding="utf-8"))
        assert evidence["concepts/AI_CPU.md"][0]["source"] == "summaries/report.md"
        assert evidence["concepts/AI_CPU.md"][0]["page"] == "2"
        assert "NVIDIA Grace CPU" in evidence["concepts/AI_CPU.md"][0]["snippet"]

    def test_apply_does_not_overwrite_existing_pages(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "concepts" / "AI_CPU.md").write_text("# Existing AI CPU", encoding="utf-8")

        created = apply_coverage_gap_concept_candidates(
            wiki,
            [{"name": "AI_CPU", "title": "AI CPU", "path": "concepts/AI_CPU.md", "action": "create"}],
        )

        assert created == []
        assert (wiki / "concepts" / "AI_CPU.md").read_text(encoding="utf-8") == "# Existing AI CPU"


class TestExtractLintFixCandidates:
    def test_ignores_deprecated_directory_scaffold_items(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()

        candidates = extract_lint_fix_candidates(
            wiki,
            "## Recommended\n- 搭建 industries/themes/metrics/risks 目录 - 至少各创建 1-2 个种子页面\n",
        )

        assert all(item["name"] != "Seed_industries" for item in candidates)

    def test_apply_lint_fix_candidates_writes_todo_free_draft_pages(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "companies").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Companies\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )

        created = apply_lint_fix_candidates(
            wiki,
            [{
                "name": "Tencent",
                "title": "Tencent",
                "path": "companies/Tencent.md",
                "action": "create",
            }],
        )

        assert created == [{
            "name": "Tencent",
            "title": "Tencent",
            "path": "companies/Tencent.md",
            "action": "created",
        }]
        text = (wiki / "companies" / "Tencent.md").read_text(encoding="utf-8")
        assert "status: draft" in text
        assert "TODO" not in text


class TestRunKnowledgeLint:
    @pytest.mark.asyncio
    async def test_returns_final_output(self, tmp_path):
        (tmp_path / "wiki").mkdir()

        mock_result = MagicMock()
        mock_result.final_output = "## Lint Report\n\nNo issues found."

        with patch("openkb.agent.linter.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await run_knowledge_lint(tmp_path, "gpt-4o-mini")

        assert "No issues found" in result

    @pytest.mark.asyncio
    async def test_calls_runner_with_correct_agent(self, tmp_path):
        (tmp_path / "wiki").mkdir()

        captured = {}

        async def fake_run(agent, message, **kwargs):
            captured["agent"] = agent
            return MagicMock(final_output="report")

        with patch("openkb.agent.linter.Runner.run", side_effect=fake_run):
            await run_knowledge_lint(tmp_path, "gpt-4o-mini")

        assert captured["agent"].name == "wiki-linter"

    @pytest.mark.asyncio
    async def test_handles_empty_final_output(self, tmp_path):
        (tmp_path / "wiki").mkdir()

        mock_result = MagicMock()
        mock_result.final_output = None

        with patch("openkb.agent.linter.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await run_knowledge_lint(tmp_path, "gpt-4o-mini")

        assert "completed" in result.lower() or result != ""
