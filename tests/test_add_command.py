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

    def test_add_local_long_pdf_runs_local_compiler_even_when_not_marked_long(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "scan-short.pdf"
        doc.write_bytes(b"%PDF")

        source_path = kb_dir / "wiki" / "sources" / "scan-short.json"
        source_path.write_text("[]", encoding="utf-8")

        from openkb.converter import ConvertResult
        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "scan-short.pdf",
            source_path=source_path,
            is_long_doc=False,
            local_long_doc=True,
            file_hash="short-local-1",
        )

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.agent.compiler.compile_local_long_doc", new_callable=AsyncMock) as mock_compile:
            mock_compile.return_value = []
            result = runner.invoke(cli, ["add", str(doc)])

        mock_compile.assert_awaited_once_with(
            "scan-short",
            source_path,
            kb_dir,
            "gpt-4o-mini",
            max_concurrency=2,
            cleanup_existing=False,
        )
        assert "OK" in result.output
        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        assert hashes["short-local-1"]["type"] == "local_long_pdf"

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

    def test_add_single_file_passes_strategy_override_to_convert_document(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "scan.pdf"
        doc.write_bytes(b"%PDF")
        source_path = kb_dir / "wiki" / "sources" / "scan.json"
        source_path.write_text("[]", encoding="utf-8")

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "scan.pdf",
            source_path=source_path,
            is_long_doc=True,
            local_long_doc=True,
        )

        with (
            patch("openkb.cli.convert_document", return_value=mock_result) as mock_convert,
            patch("openkb.agent.compiler.compile_local_long_doc", new_callable=AsyncMock) as mock_compile,
        ):
            mock_compile.return_value = []
            add_single_file(doc, kb_dir, strategy_override="ocr-local-long")

        mock_convert.assert_called_once_with(doc, kb_dir, force=False, strategy_override="ocr-local-long", job=None)

    def test_add_single_file_passes_job_to_convert_document(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")
        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")
        job = object()

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
        )

        with (
            patch("openkb.cli.convert_document", return_value=mock_result) as mock_convert,
            patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as mock_compile,
        ):
            mock_compile.return_value = []
            add_single_file(doc, kb_dir, job=job)

        mock_convert.assert_called_once_with(doc, kb_dir, force=False, strategy_override=None, job=job)

    def test_add_single_file_falls_back_to_ocr_local_long_when_pageindex_local_fails(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\n"
            "model_pool:\n"
            "  enabled: false\n",
            encoding="utf-8",
        )
        doc = tmp_path / "scan.pdf"
        doc.write_bytes(b"%PDF")
        source_path = kb_dir / "wiki" / "sources" / "scan.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text('[{"page": 1, "content": "OCR fallback"}]', encoding="utf-8")
        pageindex_input_path = tmp_path / "pageindex_input.md"
        pageindex_input_path.write_text("## Page 1\n\nOCR fallback", encoding="utf-8")

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "scan.pdf",
            source_path=source_path,
            is_long_doc=True,
            local_long_doc=False,
            selected_strategy="ocr-pageindex-local",
            pageindex_input_path=pageindex_input_path,
            file_hash="abc123",
        )

        with (
            patch("openkb.cli.convert_document", return_value=mock_result),
            patch("openkb.indexer.index_ocr_with_local_pageindex", side_effect=RuntimeError("pageindex failed")),
            patch("openkb.agent.compiler.compile_long_doc", new_callable=AsyncMock) as mock_compile_long,
            patch("openkb.agent.compiler.compile_local_long_doc", new_callable=AsyncMock) as mock_compile_local,
        ):
            mock_compile_local.return_value = []
            add_single_file(doc, kb_dir, strategy_override="ocr-pageindex-local")

        mock_compile_long.assert_not_called()
        mock_compile_local.assert_awaited_once_with(
            "scan",
            source_path,
            kb_dir,
            "gpt-4o-mini",
            max_concurrency=2,
            cleanup_existing=False,
        )

    def test_add_single_file_ocr_pageindex_local_success_runs_long_compiler_and_registers_pageindex(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult
        from openkb.indexer import IndexResult

        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "scan.pdf"
        doc.write_bytes(b"%PDF")
        source_path = kb_dir / "wiki" / "sources" / "scan.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text('[{"page": 1, "content": "OCR page"}]', encoding="utf-8")
        pageindex_input_path = tmp_path / "pageindex_input.md"
        pageindex_input_path.write_text("## Page 1\n\nOCR page", encoding="utf-8")
        summary_path = kb_dir / "wiki" / "summaries" / "scan.md"

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "scan.pdf",
            source_path=source_path,
            is_long_doc=True,
            local_long_doc=False,
            selected_strategy="ocr-pageindex-local",
            pageindex_input_path=pageindex_input_path,
            file_hash="abc123",
        )
        index_result = IndexResult(
            doc_id="local-doc-1",
            description="Local PageIndex summary",
            tree={"structure": []},
        )

        with (
            patch("openkb.cli.convert_document", return_value=mock_result),
            patch("openkb.indexer.index_ocr_with_local_pageindex", return_value=index_result) as mock_index,
            patch("openkb.agent.compiler.compile_long_doc", new_callable=AsyncMock) as mock_compile_long,
            patch("openkb.agent.compiler.compile_local_long_doc", new_callable=AsyncMock) as mock_compile_local,
        ):
            mock_compile_long.return_value = []
            add_single_file(doc, kb_dir, strategy_override="ocr-pageindex-local")

        mock_index.assert_called_once_with(
            "scan",
            source_path,
            pageindex_input_path,
            kb_dir,
            model="gpt-4o-mini",
            job=None,
        )
        mock_compile_local.assert_not_called()
        mock_compile_long.assert_awaited_once_with(
            "scan",
            summary_path,
            "local-doc-1",
            kb_dir,
            "gpt-4o-mini",
            doc_description="Local PageIndex summary",
            max_concurrency=2,
            cleanup_existing=False,
        )
        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        assert hashes["abc123"]["type"] == "long_pdf"

    def test_add_single_file_short_pdf_ocr_pageindex_local_success_runs_long_compiler_and_registers_pageindex(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult
        from openkb.indexer import IndexResult

        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "scan-short.pdf"
        doc.write_bytes(b"%PDF")
        source_path = kb_dir / "wiki" / "sources" / "scan-short.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text('[{"page": 1, "content": "OCR short page"}]', encoding="utf-8")
        pageindex_input_path = tmp_path / "pageindex_input.md"
        pageindex_input_path.write_text("## Page 1\n\nOCR short page", encoding="utf-8")
        summary_path = kb_dir / "wiki" / "summaries" / "scan-short.md"

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "scan-short.pdf",
            source_path=source_path,
            is_long_doc=False,
            local_long_doc=False,
            selected_strategy="ocr-pageindex-local",
            pageindex_input_path=pageindex_input_path,
            file_hash="short-pageindex-1",
        )
        index_result = IndexResult(
            doc_id="local-short-doc-1",
            description="Local short PageIndex summary",
            tree={"structure": []},
        )

        with (
            patch("openkb.cli.convert_document", return_value=mock_result),
            patch("openkb.indexer.index_ocr_with_local_pageindex", return_value=index_result) as mock_index,
            patch("openkb.agent.compiler.compile_long_doc", new_callable=AsyncMock) as mock_compile_long,
            patch("openkb.agent.compiler.compile_local_long_doc", new_callable=AsyncMock) as mock_compile_local,
        ):
            mock_compile_long.return_value = []
            add_single_file(doc, kb_dir, strategy_override="ocr-pageindex-local")

        mock_index.assert_called_once_with(
            "scan-short",
            source_path,
            pageindex_input_path,
            kb_dir,
            model="gpt-4o-mini",
            job=None,
        )
        mock_compile_local.assert_not_called()
        mock_compile_long.assert_awaited_once_with(
            "scan-short",
            summary_path,
            "local-short-doc-1",
            kb_dir,
            "gpt-4o-mini",
            doc_description="Local short PageIndex summary",
            max_concurrency=2,
            cleanup_existing=False,
        )
        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        assert hashes["short-pageindex-1"]["type"] == "long_pdf"

    def test_add_single_file_ocr_pageindex_local_uses_model_pool_route_model(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult
        from openkb.indexer import IndexResult
        from openkb.model_pool import record_route_success

        kb_dir = self._setup_kb(tmp_path)
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
        doc = tmp_path / "scan.pdf"
        doc.write_bytes(b"%PDF")
        source_path = kb_dir / "wiki" / "sources" / "scan.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text('[{"page": 1, "content": "OCR page"}]', encoding="utf-8")
        pageindex_input_path = tmp_path / "pageindex_input.md"
        pageindex_input_path.write_text("## Page 1\n\nOCR page", encoding="utf-8")
        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "scan.pdf",
            source_path=source_path,
            is_long_doc=True,
            local_long_doc=False,
            selected_strategy="ocr-pageindex-local",
            pageindex_input_path=pageindex_input_path,
            file_hash="abc123",
        )
        setup_profiles: list[dict[str, str]] = []
        seen_models: list[str] = []
        index_result = IndexResult(
            doc_id="local-doc-1",
            description="Local PageIndex summary",
            tree={"structure": []},
        )

        def fake_setup(_kb_dir, profile=None):
            if profile is not None:
                setup_profiles.append(profile)

        def fake_index(_doc_name, _source_path, _pageindex_input_path, _kb_dir, *, model=None, **kwargs):
            seen_models.append(model)
            if model == "bad-model":
                raise RuntimeError("upstream 500")
            return index_result

        with (
            patch("openkb.cli.convert_document", return_value=mock_result),
            patch("openkb.cli._setup_llm_key", side_effect=fake_setup),
            patch("openkb.model_pool.probe_model_route", side_effect=RuntimeError("probe failed")) as probe,
            patch("openkb.indexer.index_ocr_with_local_pageindex", side_effect=fake_index),
            patch("openkb.agent.compiler.compile_local_long_doc", new_callable=AsyncMock, return_value=[]),
            patch("openkb.agent.compiler.compile_long_doc", new_callable=AsyncMock, return_value=[]),
        ):
            add_single_file(doc, kb_dir, strategy_override="ocr-pageindex-local")

        assert seen_models == ["bad-model", "good-model"]
        assert probe.call_args.args[1].model == "bad-model"
        setup_ids = [profile["id"] for profile in setup_profiles]
        assert "primary" in setup_ids
        assert "backup" in setup_ids
        assert setup_ids.index("primary") < setup_ids.index("backup")

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

        mock_convert.assert_called_once_with(doc, kb_dir, force=True, strategy_override=None, job=None)
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

    def test_add_single_file_falls_back_to_next_llm_profile_on_retryable_failure(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult

        class RetryableLLMError(RuntimeError):
            status_code = 500

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "active_llm_profile: bad\n"
            "model: bad-model\n"
            "wire_api: chat_completions\n"
            "base_url: https://bad.example.com/v1\n"
            "model_pool:\n"
            "  enabled: false\n"
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
        (kb_dir / ".env").write_text(
            "OPENKB_LLM_PROFILE_BAD_API_KEY=bad-key\n"
            "OPENKB_LLM_PROFILE_GOOD_API_KEY=good-key\n",
            encoding="utf-8",
        )
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")
        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")
        events: list[str] = []
        calls: list[str] = []

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
            file_hash="abc123",
        )

        async def compile_short_doc(_doc_name, _source_path, _kb_dir, model, **_kwargs):
            calls.append(model)
            if model == "bad-model":
                raise RetryableLLMError("upstream 500")
            return []

        with patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.agent.compiler.compile_short_doc", side_effect=compile_short_doc):
            add_single_file(doc, kb_dir, strict=True, progress_callback=events.append)

        assert calls == ["bad-model", "bad-model", "good-model"]
        assert any("Retrying with LLM profile Good" in event for event in events)
        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        assert hashes["abc123"]["name"] == "test.md"

    def test_add_single_file_uses_model_pool_routes_and_records_health(self, tmp_path):
        from openkb.cli import add_single_file
        from openkb.converter import ConvertResult
        from openkb.model_pool import record_route_success

        kb_dir = self._setup_kb(tmp_path)
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
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")
        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")
        calls: list[str] = []
        setup_profiles: list[dict[str, str]] = []

        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
            file_hash="abc123",
        )

        async def compile_short_doc(_doc_name, _source_path, _kb_dir, model, **_kwargs):
            calls.append(model)
            if model == "bad-model":
                raise RuntimeError("upstream 500")
            return []

        def fake_setup(_kb_dir, profile):
            setup_profiles.append(profile)

        with patch("openkb.cli.convert_document", return_value=mock_result), \
             patch("openkb.cli._setup_llm_key", side_effect=fake_setup), \
             patch("openkb.model_pool.probe_model_route", side_effect=RuntimeError("probe failed")) as probe, \
             patch("openkb.agent.compiler.compile_short_doc", side_effect=compile_short_doc):
            add_single_file(doc, kb_dir, strict=True)

        assert calls == ["bad-model", "good-model"]
        assert probe.call_count == 1
        assert probe.call_args.args[1].model == "bad-model"
        assert setup_profiles[-2]["id"] == "primary"
        assert setup_profiles[-2]["model"] == "bad-model"
        assert setup_profiles[-1]["id"] == "backup"
        assert setup_profiles[-1]["model"] == "good-model"
        status = json.loads((kb_dir / ".openkb" / "model-pool" / "status.json").read_text(encoding="utf-8"))
        assert status["routes"]["primary:bad-model"]["health"] == "offline"
        assert status["routes"]["backup:good-model"]["health"] == "healthy"
        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        assert hashes["abc123"]["name"] == "test.md"


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
