"""Tests for openkb list and openkb status CLI commands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from openkb.cli import cli


def _setup_kb(tmp_path: Path) -> Path:
    """Create a minimal KB structure and return kb_dir."""
    kb_dir = tmp_path
    (kb_dir / "raw").mkdir()
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / "wiki" / "companies").mkdir(parents=True)
    (kb_dir / "wiki" / "industries").mkdir(parents=True)
    (kb_dir / "wiki" / "themes").mkdir(parents=True)
    (kb_dir / "wiki" / "metrics").mkdir(parents=True)
    (kb_dir / "wiki" / "risks").mkdir(parents=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True)
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
    (openkb_dir / "hashes.json").write_text(json.dumps({}))
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n"
    )
    return kb_dir


class TestListCommand:
    def test_list_no_kb(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path), \
             patch("openkb.cli._find_kb_dir", return_value=None):
            result = runner.invoke(cli, ["list"])
            assert "No knowledge base found" in result.output

    def test_list_empty_kb(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["list"])
            assert "No documents indexed yet" in result.output

    def test_list_shows_documents(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {
            "abc123": {"name": "paper.pdf", "type": "pdf"},
            "def456": {"name": "notes.md", "type": "md"},
        }
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["list"])

        assert "paper.pdf" in result.output
        assert "notes.md" in result.output
        assert "pdf" in result.output
        assert "md" in result.output

    def test_list_displays_local_long_documents(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {
            "abc123": {"name": "research.pdf", "type": "local_long_pdf"},
        }
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["list"])

        assert "research.pdf" in result.output
        assert "local-long" in result.output

    def test_list_shows_concepts(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / "wiki" / "concepts" / "attention.md").write_text("# Attention")
        (kb_dir / "wiki" / "concepts" / "transformer.md").write_text("# Transformer")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["list"])

        assert "attention" in result.output
        assert "transformer" in result.output

    def test_list_no_concepts_section_when_empty(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["list"])

        assert result.exit_code == 0
        # No concepts in output since none exist
        assert "Concepts:" not in result.output

    def test_list_shows_companies(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / "wiki" / "companies" / "tsmc.md").write_text("# TSMC")
        (kb_dir / "wiki" / "companies" / "smic.md").write_text("# SMIC")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["list"])

        assert "Companies (2):" in result.output
        assert "tsmc" in result.output
        assert "smic" in result.output

    def test_list_shows_expanded_investment_schema_pages(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / "wiki" / "industries" / "semiconductors.md").write_text("# Semiconductors")
        (kb_dir / "wiki" / "themes" / "ai-capex.md").write_text("# AI CAPEX")
        (kb_dir / "wiki" / "metrics" / "gross-margin.md").write_text("# Gross Margin")
        (kb_dir / "wiki" / "risks" / "export-controls.md").write_text("# Export Controls")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["list"])

        assert "Industries (1):" in result.output
        assert "semiconductors" in result.output
        assert "Themes (1):" in result.output
        assert "ai-capex" in result.output
        assert "Metrics (1):" in result.output
        assert "gross-margin" in result.output
        assert "Risks (1):" in result.output
        assert "export-controls" in result.output


class TestStatusCommand:
    def test_status_no_kb(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path), \
             patch("openkb.cli._find_kb_dir", return_value=None):
            result = runner.invoke(cli, ["status"])
            assert "No knowledge base found" in result.output

    def test_status_shows_directory_counts(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        # Add some files
        (kb_dir / "wiki" / "sources" / "doc1.md").write_text("# Doc 1")
        (kb_dir / "wiki" / "sources" / "doc2.md").write_text("# Doc 2")
        (kb_dir / "wiki" / "summaries" / "sum1.md").write_text("# Sum 1")
        (kb_dir / "wiki" / "concepts" / "concept1.md").write_text("# Concept")
        (kb_dir / "wiki" / "companies" / "company1.md").write_text("# Company")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["status"])

        assert "sources" in result.output
        assert "summaries" in result.output
        assert "concepts" in result.output
        assert "companies" in result.output
        assert "industries" in result.output
        assert "themes" in result.output
        assert "metrics" in result.output
        assert "risks" in result.output
        assert "reports" in result.output

    def test_status_shows_total_indexed(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {
            "abc": {"name": "a.pdf", "type": "pdf"},
            "def": {"name": "b.pdf", "type": "pdf"},
            "ghi": {"name": "c.md", "type": "md"},
        }
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["status"])

        assert "3" in result.output  # total indexed count

    def test_status_shows_raw_count(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        (kb_dir / "raw" / "file1.pdf").write_bytes(b"PDF")
        (kb_dir / "raw" / "file2.pdf").write_bytes(b"PDF")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["status"])

        assert "raw" in result.output

    def test_status_exit_code_zero(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0
