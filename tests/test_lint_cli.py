"""Tests for the openkb lint CLI command."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from openkb.cli import _safe_echo, cli


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _setup_kb(tmp_path: Path) -> Path:
    """Create a minimal KB structure and return kb_dir."""
    kb_dir = tmp_path
    (kb_dir / "raw").mkdir()
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True)
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
    (openkb_dir / "hashes.json").write_text(json.dumps({}))
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n"
    )
    return kb_dir


class TestLintCommand:
    def test_safe_echo_replaces_unencodable_text(self, monkeypatch):
        calls = []

        def fake_echo(text="", **kwargs):
            calls.append(str(text))
            if "🔍" in str(text):
                raise UnicodeEncodeError("gbk", "🔍", 0, 1, "illegal multibyte sequence")

        monkeypatch.setattr("openkb.cli.click.echo", fake_echo)

        _safe_echo("🔍 issue")

        assert calls == ["🔍 issue", "? issue"]

    def test_lint_empty_kb_skips(self, tmp_path):
        """Lint on an empty KB (no indexed docs) should exit early."""
        kb_dir = _setup_kb(tmp_path)
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["lint"])
        assert result.exit_code == 0
        assert "Nothing to lint" in result.output
        assert "no documents indexed" in result.output
        # No report should be written
        reports = list((kb_dir / "wiki" / "reports").glob("*.md"))
        assert reports == []

    def test_lint_no_hashes_file_skips(self, tmp_path):
        """Lint should also skip when hashes.json doesn't exist."""
        kb_dir = _setup_kb(tmp_path)
        (kb_dir / ".openkb" / "hashes.json").unlink()
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["lint"])
        assert result.exit_code == 0
        assert "Nothing to lint" in result.output

    def test_lint_no_kb(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path), \
             patch("openkb.cli._find_kb_dir", return_value=None):
            result = runner.invoke(cli, ["lint"])
            assert "No knowledge base found" in result.output

    def test_lint_runs_when_docs_exist(self, tmp_path):
        """Lint should proceed when there are indexed documents."""
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value="No issues."):
            result = runner.invoke(cli, ["lint"])
        assert result.exit_code == 0
        assert "Running structural lint" in result.output
        assert "Running knowledge lint" in result.output
        assert "Report written to" in result.output
        tracked = set(_git(kb_dir, "ls-files").splitlines())
        assert "wiki/log.md" in tracked
        assert any(path.startswith("wiki/reports/lint_") for path in tracked)
        assert not any(path.startswith("raw/") for path in tracked)
        assert _git(kb_dir, "log", "-1", "--pretty=%s") == "Run lint"

    def test_lint_report_includes_coverage_gap_candidates(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        runner = CliRunner()
        semantic_report = "## Gaps\n- Missing concept page for AI CPU and global AI GPU."

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value=semantic_report):
            result = runner.invoke(cli, ["lint"])

        assert result.exit_code == 0
        assert "Coverage Gap Candidates" in result.output
        assert "[[concepts/AI_CPU]]" in result.output
        reports = list((kb_dir / "wiki" / "reports").glob("lint_*.md"))
        assert len(reports) == 1
        report_text = reports[0].read_text(encoding="utf-8")
        assert "[[concepts/AI_GPU]]" in report_text

    @pytest.mark.asyncio
    async def test_run_lint_falls_back_to_next_llm_profile_on_retryable_failure(self, tmp_path):
        from openkb.cli import run_lint

        class RetryableLLMError(RuntimeError):
            status_code = 500

        kb_dir = _setup_kb(tmp_path)
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({"abc": {"name": "paper.pdf", "type": "pdf"}}))
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "active_llm_profile: bad\n"
            "model: bad-model\n"
            "wire_api: chat_completions\n"
            "base_url: https://bad.example.com/v1\n"
            "llm_profiles:\n"
            "- id: bad\n"
            "  name: Bad\n"
            "  model: bad-model\n"
            "  wire_api: chat_completions\n"
            "  base_url: https://bad.example.com/v1\n"
            "  api_key_env: OPENKB_LLM_PROFILE_BAD_API_KEY\n"
            "- id: good\n"
            "  name: Good\n"
            "  model: good-model\n"
            "  wire_api: chat_completions\n"
            "  base_url: https://good.example.com/v1\n"
            "  api_key_env: OPENKB_LLM_PROFILE_GOOD_API_KEY\n",
            encoding="utf-8",
        )
        calls: list[str] = []

        async def run_knowledge_lint(_kb_dir, model):
            calls.append(model)
            if model == "bad-model":
                raise RetryableLLMError("upstream 500")
            return "No issues."

        with patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", side_effect=run_knowledge_lint):
            report_path = await run_lint(kb_dir)

        assert calls == ["bad-model", "good-model"]
        assert report_path is not None
        report_text = report_path.read_text(encoding="utf-8")
        assert "No issues." in report_text
        assert "Knowledge lint failed" not in report_text

    @pytest.mark.asyncio
    async def test_run_lint_uses_model_pool_routes_and_records_health(self, tmp_path):
        from openkb.cli import run_lint
        from openkb.model_pool import record_route_success

        kb_dir = _setup_kb(tmp_path)
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({"abc": {"name": "paper.pdf", "type": "pdf"}}))
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "active_llm_profile: primary\n"
            "model: fallback-model\n"
            "wire_api: chat_completions\n"
            "model_pool:\n"
            "  enabled: true\n"
            "  strategy: weighted_round_robin\n"
            "llm_profiles:\n"
            "- id: primary\n"
            "  name: Primary\n"
            "  model: bad-model\n"
            "  wire_api: chat_completions\n"
            "  base_url: https://bad.example.com/v1\n"
            "  api_key_env: OPENKB_LLM_PROFILE_PRIMARY_API_KEY\n"
            "  models:\n"
            "  - name: bad-model\n"
            "    weight: 100\n"
            "- id: backup\n"
            "  name: Backup\n"
            "  model: good-model\n"
            "  wire_api: chat_completions\n"
            "  base_url: https://good.example.com/v1\n"
            "  api_key_env: OPENKB_LLM_PROFILE_BACKUP_API_KEY\n"
            "  models:\n"
            "  - name: good-model\n"
            "    weight: 100\n",
            encoding="utf-8",
        )
        record_route_success(kb_dir, "primary", "bad-model", latency_ms=10)
        record_route_success(kb_dir, "backup", "good-model", latency_ms=20)
        calls: list[str] = []
        setup_profiles: list[dict[str, str]] = []

        async def run_knowledge_lint(_kb_dir, model):
            calls.append(model)
            if model == "bad-model":
                raise RuntimeError("upstream 500")
            return "No issues."

        def fake_setup(_kb_dir, profile):
            setup_profiles.append(profile)

        with patch("openkb.cli._setup_llm_key", side_effect=fake_setup), \
             patch("openkb.agent.linter.run_knowledge_lint", side_effect=run_knowledge_lint):
            report_path = await run_lint(kb_dir)

        assert calls == ["bad-model", "good-model"]
        assert setup_profiles[-2]["id"] == "primary"
        assert setup_profiles[-2]["model"] == "bad-model"
        assert setup_profiles[-1]["id"] == "backup"
        assert setup_profiles[-1]["model"] == "good-model"
        assert report_path is not None
        assert "No issues." in report_path.read_text(encoding="utf-8")
        status = json.loads((kb_dir / ".openkb" / "model-pool" / "status.json").read_text(encoding="utf-8"))
        assert status["routes"]["primary:bad-model"]["health"] == "offline"
        assert status["routes"]["backup:good-model"]["health"] == "healthy"

    def test_lint_fix_skips_coverage_gap_drafts_without_source_evidence(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        runner = CliRunner()
        semantic_report = "## Gaps\n- Missing concept page for AI CPU and global AI GPU."

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value=semantic_report):
            result = runner.invoke(cli, ["lint", "--fix"])

        assert result.exit_code == 0
        assert "not yet implemented" not in result.output
        assert "Created coverage-gap draft concept page(s)" not in result.output
        assert not (kb_dir / "wiki" / "concepts" / "AI_CPU.md").exists()
        assert not (kb_dir / "wiki" / "concepts" / "AI_GPU.md").exists()
        index = (kb_dir / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "[[concepts/AI_CPU]]" not in index
        reports = list((kb_dir / "wiki" / "reports").glob("lint_*.md"))
        report_text = reports[0].read_text(encoding="utf-8")
        assert "## Coverage Gap Fixes" in report_text

    def test_lint_fix_creates_coverage_gap_draft_pages_with_source_evidence(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / "wiki" / "summaries" / "report.md").write_text(
            "---\ndoc_type: local-long\n---\n\n"
            "# Report\n\n"
            "AI CPU roadmaps and global AI GPU demand are both discussed in this source.",
            encoding="utf-8",
        )
        runner = CliRunner()
        semantic_report = "## Gaps\n- Missing concept page for AI CPU and global AI GPU."

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value=semantic_report):
            result = runner.invoke(cli, ["lint", "--fix"])

        assert result.exit_code == 0
        assert "Created coverage-gap draft concept page(s): 2" in result.output
        assert (kb_dir / "wiki" / "concepts" / "AI_CPU.md").exists()
        assert (kb_dir / "wiki" / "concepts" / "AI_GPU.md").exists()
        index = (kb_dir / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "[[concepts/AI_CPU]]" in index
        reports = list((kb_dir / "wiki" / "reports").glob("lint_*.md"))
        report_text = reports[0].read_text(encoding="utf-8")
        assert "## Coverage Gap Fixes" in report_text

    def test_lint_fix_cleans_legacy_generated_placeholders(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / "wiki" / "companies").mkdir(exist_ok=True)
        (kb_dir / "wiki" / "companies" / "Tencent.md").write_text(
            "# Tencent\n\n"
            "## Source Evidence\n"
            "- [[summaries/report]]: TODO: add exact supporting claims and page references.\n",
            encoding="utf-8",
        )
        runner = CliRunner()

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value="No issues."):
            result = runner.invoke(cli, ["lint", "--fix"])

        assert result.exit_code == 0
        company_text = (kb_dir / "wiki" / "companies" / "Tencent.md").read_text(encoding="utf-8")
        assert "add exact supporting claims" not in company_text
        assert "Legacy Placeholder Fixes" in result.output
        reports = list((kb_dir / "wiki" / "reports").glob("lint_*.md"))
        report_text = reports[0].read_text(encoding="utf-8")
        assert "companies/Tencent.md" in report_text

    def test_lint_fix_help_describes_safe_draft_generation(self):
        runner = CliRunner()

        result = runner.invoke(cli, ["lint", "--help"])

        assert result.exit_code == 0
        assert "not yet implemented" not in result.output
        assert "draft concept pages" in result.output
