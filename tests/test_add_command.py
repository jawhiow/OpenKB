"""Tests for the `add` CLI command (Task 10)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from openkb.cli import SUPPORTED_EXTENSIONS, _find_kb_dir, cli


class TestSupportedExtensions:
    def test_pdf_supported(self):
        assert ".pdf" in SUPPORTED_EXTENSIONS

    def test_md_supported(self):
        assert ".md" in SUPPORTED_EXTENSIONS

    def test_docx_supported(self):
        assert ".docx" in SUPPORTED_EXTENSIONS

    def test_txt_supported(self):
        assert ".txt" in SUPPORTED_EXTENSIONS

    def test_unknown_not_supported(self):
        assert ".xyz" not in SUPPORTED_EXTENSIONS


class TestFindKbDir:
    def test_finds_openkb_dir(self, tmp_path, monkeypatch):
        (tmp_path / ".openkb").mkdir()
        monkeypatch.chdir(tmp_path)
        result = _find_kb_dir()
        assert result is not None

    def test_returns_none_if_no_openkb(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("openkb.cli.load_global_config", return_value={}):
            result = _find_kb_dir()
            assert result is None


class TestAddCommand:
    def _setup_kb(self, tmp_path):
        """Create a minimal KB structure."""
        (tmp_path / "raw").mkdir()
        (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
        (tmp_path / "wiki" / "summaries").mkdir(parents=True)
        (tmp_path / "wiki" / "concepts").mkdir(parents=True)
        (tmp_path / "wiki" / "reports").mkdir(parents=True)
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
        (openkb_dir / "hashes.json").write_text(json.dumps({}))
        return tmp_path

    def test_add_missing_init(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path), \
             patch("openkb.cli._find_kb_dir", return_value=None):
            result = runner.invoke(cli, ["add", "somefile.pdf"])
            assert "No knowledge base found" in result.output

    def test_add_single_file_calls_helper(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")

        runner = CliRunner()
        with patch("openkb.cli.add_single_file") as mock_add, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(doc)])
            mock_add.assert_called_once_with(doc, kb_dir)

    def test_add_force_passes_force_to_helper(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")

        runner = CliRunner()
        with patch("openkb.cli.add_single_file") as mock_add, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", "--force", str(doc)])
            mock_add.assert_called_once_with(doc, kb_dir, force=True)

    def test_add_directory_calls_helper_for_each_file(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("# A")
        (docs_dir / "b.txt").write_text("B content")
        (docs_dir / "ignore.xyz").write_text("skip me")

        runner = CliRunner()
        with patch("openkb.cli.add_single_file") as mock_add, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(docs_dir)])
            # Should be called for .md and .txt but not .xyz
            assert mock_add.call_count == 2
            called_names = {call.args[0].name for call in mock_add.call_args_list}
            assert "a.md" in called_names
            assert "b.txt" in called_names
            assert "ignore.xyz" not in called_names

    def test_add_unsupported_extension(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "file.xyz"
        doc.write_text("content")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(doc)])
            assert "Unsupported file type" in result.output

    def test_add_nonexistent_path(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(tmp_path / "nonexistent.pdf")])
            assert "does not exist" in result.output

    def test_add_skipped_file(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")

        from openkb.converter import ConvertResult
        mock_result = ConvertResult(skipped=True)

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.convert_document", return_value=mock_result) as mock_conv, \
             patch("openkb.cli.asyncio.run") as mock_arun:
            result = runner.invoke(cli, ["add", str(doc)])
            assert "SKIP" in result.output
            mock_arun.assert_not_called()

    def test_add_short_doc_runs_compiler(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")

        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")

        from openkb.converter import ConvertResult
        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
        )

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as mock_compile:
            result = runner.invoke(cli, ["add", str(doc)])
            mock_compile.assert_awaited_once()
            assert "OK" in result.output

    def test_add_local_long_pdf_runs_local_compiler_and_registers_type(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "report.pdf"
        doc.write_bytes(b"%PDF")

        source_path = kb_dir / "wiki" / "sources" / "report.json"
        source_path.write_text("[]", encoding="utf-8")

        from openkb.converter import ConvertResult
        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "report.pdf",
            source_path=source_path,
            is_long_doc=True,
            local_long_doc=True,
            file_hash="abc123",
        )

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.agent.compiler.compile_local_long_doc", new_callable=AsyncMock) as mock_compile:
            mock_compile.return_value = []
            result = runner.invoke(cli, ["add", str(doc)])

        mock_compile.assert_awaited_once_with(
            "report",
            source_path,
            kb_dir,
            "gpt-4o-mini",
            max_concurrency=2,
            cleanup_existing=False,
        )
        assert "local page index" in result.output
        assert "OK" in result.output

        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        assert hashes["abc123"]["type"] == "local_long_pdf"

    def test_add_single_file_strict_raises_compilation_failure_with_progress(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")
        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")
        events: list[str] = []

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
        )

        with patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.agent.compiler.compile_short_doc", side_effect=RuntimeError("llm timeout")):
            with pytest.raises(RuntimeError, match="Compilation failed"):
                add_single_file(doc, kb_dir, strict=True, progress_callback=events.append)

        assert any("Converting" in event for event in events)
        assert any("Compiling short document" in event for event in events)

    def test_add_force_preserves_generated_pages_when_compilation_fails(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / "wiki" / "companies").mkdir(parents=True)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")
        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")
        index_path = kb_dir / "wiki" / "index.md"
        original_index = (
            "# Index\n\n"
            "## Documents\n- [[summaries/test]] - Old summary\n\n"
            "## Companies\n- [[companies/TSMC]] - Old company\n\n"
            "## Concepts\n- [[concepts/HBM]] - Old concept\n\n"
        )
        index_path.write_text(original_index, encoding="utf-8")
        company_path = kb_dir / "wiki" / "companies" / "TSMC.md"
        concept_path = kb_dir / "wiki" / "concepts" / "HBM.md"
        company_text = "---\nsources: [summaries/test.md]\n---\n\n# TSMC\n"
        concept_text = "---\nsources: [summaries/test.md]\n---\n\n# HBM\n"
        company_path.write_text(company_text, encoding="utf-8")
        concept_path.write_text(concept_text, encoding="utf-8")

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
        )

        with patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.agent.compiler.compile_short_doc", side_effect=RuntimeError("llm timeout")):
            with pytest.raises(RuntimeError, match="Compilation failed"):
                add_single_file(doc, kb_dir, force=True, strict=True)

        assert company_path.read_text(encoding="utf-8") == company_text
        assert concept_path.read_text(encoding="utf-8") == concept_text
        assert index_path.read_text(encoding="utf-8") == original_index

    def test_add_single_file_force_passes_force_to_converter(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")
        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
        )

        with patch("openkb.cli.convert_document", return_value=mock_result) as mock_convert, \
             patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as mock_compile:
            mock_compile.return_value = []
            add_single_file(doc, kb_dir, force=True)

        mock_convert.assert_called_once_with(doc, kb_dir, force=True)
        mock_compile.assert_awaited_once_with(
            "test",
            source_path,
            kb_dir,
            "gpt-4o-mini",
            max_concurrency=2,
            cleanup_existing=True,
        )

    def test_add_single_file_passes_configured_compile_concurrency(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\ncompile_max_concurrency: 3\n",
            encoding="utf-8",
        )
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")
        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
        )

        with patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as mock_compile:
            mock_compile.return_value = []
            add_single_file(doc, kb_dir)

        mock_compile.assert_awaited_once_with(
            "test",
            source_path,
            kb_dir,
            "gpt-4o-mini",
            max_concurrency=3,
            cleanup_existing=False,
        )


class TestRebuildCommand:
    def _setup_kb(self, tmp_path):
        (tmp_path / "raw").mkdir()
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
        (openkb_dir / "hashes.json").write_text(json.dumps({}))
        return tmp_path

    def test_rebuild_missing_init(self, tmp_path):
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=None):
            result = runner.invoke(cli, ["rebuild"])
        assert "No knowledge base found" in result.output

    def test_rebuild_forces_all_supported_raw_files(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / "raw" / "a.md").write_text("# A")
        (kb_dir / "raw" / "b.txt").write_text("B")
        (kb_dir / "raw" / "ignore.xyz").write_text("skip")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.add_single_file") as mock_add:
            result = runner.invoke(cli, ["rebuild"])

        assert "Rebuilding 2 raw document(s)" in result.output
        assert mock_add.call_count == 2
        called = [(call.args[0].name, call.args[1], call.kwargs) for call in mock_add.call_args_list]
        assert called == [
            ("a.md", kb_dir, {"force": True}),
            ("b.txt", kb_dir, {"force": True}),
        ]

    def test_rebuild_strict_passes_strict_to_helper(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / "raw" / "a.md").write_text("# A")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.add_single_file") as mock_add:
            result = runner.invoke(cli, ["rebuild", "--strict"])

        assert result.exit_code == 0
        mock_add.assert_called_once_with(kb_dir / "raw" / "a.md", kb_dir, force=True, strict=True)
