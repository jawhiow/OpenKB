"""Tests for openkb.converter."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openkb.converter import ConvertResult, convert_document, get_pdf_page_count
from openkb.ocr.detect import is_probably_scanned_pdf


# ---------------------------------------------------------------------------
# get_pdf_page_count
# ---------------------------------------------------------------------------


class TestGetPdfPageCount:
    def test_returns_page_count(self, tmp_path):
        """Mock pymupdf to return a doc with 5 pages."""
        fake_doc = MagicMock()
        fake_doc.page_count = 5
        fake_doc.__enter__ = MagicMock(return_value=fake_doc)
        fake_doc.__exit__ = MagicMock(return_value=False)
        with patch("openkb.converter.pymupdf.open", return_value=fake_doc):
            count = get_pdf_page_count(tmp_path / "fake.pdf")
        assert count == 5


class TestScanDetection:
    def test_detects_scanned_pdf_when_sampled_pages_have_little_text(self, tmp_path):
        pdf_path = tmp_path / "scan.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake scanned")

        class FakePage:
            def __init__(self, text):
                self._text = text

            def get_text(self, mode):
                assert mode == "text"
                return self._text

        class FakeDoc:
            def __init__(self, pages):
                self._pages = pages
                self.page_count = len(pages)

            def __len__(self):
                return len(self._pages)

            def __getitem__(self, index):
                return self._pages[index]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        fake_doc = FakeDoc([FakePage(""), FakePage("  "), FakePage("\n")])

        with patch("openkb.ocr.detect.pymupdf.open", return_value=fake_doc):
            assert is_probably_scanned_pdf(pdf_path) is True

    def test_detects_digital_pdf_when_sampled_pages_have_text(self, tmp_path):
        pdf_path = tmp_path / "digital.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake digital")

        class FakePage:
            def __init__(self, text):
                self._text = text

            def get_text(self, mode):
                assert mode == "text"
                return self._text

        class FakeDoc:
            def __init__(self, pages):
                self._pages = pages
                self.page_count = len(pages)

            def __len__(self):
                return len(self._pages)

            def __getitem__(self, index):
                return self._pages[index]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        fake_doc = FakeDoc(
            [
                FakePage("Revenue grew 15% year over year across the quarter."),
                FakePage("Gross margin expanded and capex guidance was reiterated."),
                FakePage("Valuation remains attractive relative to historical multiples."),
            ]
        )

        with patch("openkb.ocr.detect.pymupdf.open", return_value=fake_doc):
            assert is_probably_scanned_pdf(pdf_path) is False


# ---------------------------------------------------------------------------
# convert_document — .md input
# ---------------------------------------------------------------------------


class TestConvertDocumentMarkdown:
    def test_md_file_copied_to_wiki_sources(self, kb_dir):
        """A .md file is read and saved under wiki/sources/."""
        src = kb_dir / "raw" / "notes.md"
        src.write_text("# Notes\n\nSome content here.", encoding="utf-8")

        result = convert_document(src, kb_dir)

        assert result.skipped is False
        assert result.is_long_doc is False
        assert result.source_path is not None
        assert result.source_path.exists()
        assert result.source_path.read_text(encoding="utf-8").startswith("# Notes")

    def test_md_duplicate_skipped(self, kb_dir):
        """Second call with same file returns skipped=True when hash is registered."""
        from openkb.state import HashRegistry

        src = kb_dir / "raw" / "notes.md"
        src.write_text("# Notes\n\nSome content here.", encoding="utf-8")

        result1 = convert_document(src, kb_dir)  # first call
        # Simulate CLI registering the hash after successful compilation
        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        registry.add(result1.file_hash, {"name": src.name, "type": "md"})

        result2 = convert_document(src, kb_dir)  # second call
        assert result2.skipped is True
        assert result2.source_path is None
        assert result2.raw_path is None

    def test_md_duplicate_can_be_forced(self, kb_dir):
        """force=True bypasses the hash skip so known documents can be recompiled."""
        from openkb.state import HashRegistry

        src = kb_dir / "raw" / "notes.md"
        src.write_text("# Notes\n\nSome content here.", encoding="utf-8")

        result1 = convert_document(src, kb_dir)
        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        registry.add(result1.file_hash, {"name": src.name, "type": "md"})

        result2 = convert_document(src, kb_dir, force=True)

        assert result2.skipped is False
        assert result2.file_hash == result1.file_hash
        assert result2.source_path is not None
        assert result2.source_path.exists()

    def test_md_raw_file_copied(self, kb_dir):
        """The original file should also be copied to raw/."""
        src = kb_dir / "input" / "notes.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("# Notes\n", encoding="utf-8")

        result = convert_document(src, kb_dir)

        assert result.raw_path is not None
        assert result.raw_path.exists()


# ---------------------------------------------------------------------------
# convert_document — PDF short doc
# ---------------------------------------------------------------------------


class TestConvertDocumentPdfShort:
    def test_short_pdf_converted_via_pymupdf(self, kb_dir, tmp_path):
        """PDF under threshold is converted with pymupdf (convert_pdf_with_images)."""
        src = tmp_path / "short.pdf"
        src.write_bytes(b"%PDF-1.4 fake content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter.convert_pdf_with_images", return_value="# Short PDF\n\nConverted.") as mock_cpwi,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 5  # below default threshold of 20
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir)

        mock_cpwi.assert_called_once()
        assert result.skipped is False
        assert result.is_long_doc is False
        assert result.source_path is not None
        assert result.source_path.exists()

    def test_short_scanned_pdf_uses_ocr_pageindex_below_threshold(self, kb_dir, tmp_path):
        src = tmp_path / "short-scan.pdf"
        src.write_bytes(b"%PDF-1.4 fake scanned content")
        ocr_pages_path = tmp_path / "ocr-pages.json"
        ocr_pages_path.write_text(
            json.dumps([{"page": 1, "content": "OCR short page", "images": []}], ensure_ascii=False),
            encoding="utf-8",
        )
        ocr_md_path = tmp_path / "ocr-input.md"
        ocr_md_path.write_text("## Page 1\n\nOCR short page", encoding="utf-8")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter.is_probably_scanned_pdf", return_value=True),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": ocr_pages_path,
                    "pageindex_input_path": ocr_md_path,
                },
            ) as mock_prepare,
            patch("openkb.converter.convert_pdf_with_images") as mock_cpwi,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 5
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir)

        mock_prepare.assert_called_once()
        mock_cpwi.assert_not_called()
        assert result.is_long_doc is False
        assert result.local_long_doc is False
        assert result.selected_strategy == "ocr-pageindex-local"
        assert result.pageindex_input_path == ocr_md_path
        assert result.source_path is not None
        assert result.source_path.suffix == ".json"
        assert "OCR short page" in result.source_path.read_text(encoding="utf-8")

    def test_short_pdf_plain_local_long_override_uses_page_json_below_threshold(self, kb_dir, tmp_path):
        src = tmp_path / "short-plain-local.pdf"
        src.write_bytes(b"%PDF-1.4 fake local-long content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch(
                "openkb.converter.convert_pdf_to_pages",
                return_value=[{"page": 1, "content": "Local page one", "images": []}],
            ) as mock_pages,
            patch("openkb.converter.convert_pdf_with_images") as mock_cpwi,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 5
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir, strategy_override="plain-local-long")

        mock_pages.assert_called_once()
        mock_cpwi.assert_not_called()
        assert result.is_long_doc is False
        assert result.local_long_doc is True
        assert result.selected_strategy == "plain-local-long"
        assert result.source_path is not None
        assert result.source_path.suffix == ".json"
        assert "Local page one" in result.source_path.read_text(encoding="utf-8")

    def test_short_pdf_ocr_pageindex_local_override_prepares_ocr_artifacts_below_threshold(self, kb_dir, tmp_path):
        src = tmp_path / "short-pageindex.pdf"
        src.write_bytes(b"%PDF-1.4 fake short content")
        ocr_pages_path = tmp_path / "ocr-pages.json"
        ocr_pages_path.write_text(
            json.dumps([{"page": 1, "content": "OCR short pageindex", "images": []}], ensure_ascii=False),
            encoding="utf-8",
        )
        ocr_md_path = tmp_path / "ocr-input.md"
        ocr_md_path.write_text("## Page 1\n\nOCR short pageindex", encoding="utf-8")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter.is_probably_scanned_pdf", return_value=False),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": ocr_pages_path,
                    "pageindex_input_path": ocr_md_path,
                },
            ) as mock_prepare,
            patch("openkb.converter.convert_pdf_with_images") as mock_cpwi,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 5
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir, strategy_override="ocr-pageindex-local")

        mock_prepare.assert_called_once()
        mock_cpwi.assert_not_called()
        assert result.is_long_doc is False
        assert result.local_long_doc is False
        assert result.selected_strategy == "ocr-pageindex-local"
        assert result.pageindex_input_path == ocr_md_path
        assert result.source_path is not None
        assert "OCR short pageindex" in result.source_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# convert_document — PDF long doc
# ---------------------------------------------------------------------------


class TestConvertDocumentPdfLong:
    def test_long_pdf_returns_is_long_doc(self, kb_dir, tmp_path):
        """PDF >= threshold pages returns is_long_doc=True, source_path=None."""
        src = tmp_path / "long.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=True),
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200  # above threshold
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir)

        assert result.is_long_doc is True
        assert result.source_path is None
        assert result.skipped is False
        assert result.raw_path is not None

    def test_long_pdf_requires_pageindex_backend(self, kb_dir, tmp_path):
        """Long PDFs fail clearly instead of creating local-long documents."""
        src = tmp_path / "fallback.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=False),
            patch("openkb.converter.convert_pdf_to_pages") as mock_pages,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            with pytest.raises(RuntimeError, match="requires PageIndex"):
                convert_document(src, kb_dir)

        mock_pages.assert_not_called()

    def test_long_pdf_marks_scanned_recommendation_when_ocr_enabled(self, kb_dir, tmp_path):
        src = tmp_path / "scan-recommend.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=False),
            patch("openkb.converter.is_probably_scanned_pdf", return_value=True),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": tmp_path / "prepared-pages.json",
                    "pageindex_input_path": tmp_path / "prepared-input.md",
                },
            ),
            patch("openkb.converter.convert_pdf_to_pages", return_value=[]),
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc
            (tmp_path / "prepared-pages.json").write_text("[]", encoding="utf-8")
            (tmp_path / "prepared-input.md").write_text("## Page 1", encoding="utf-8")

            result = convert_document(src, kb_dir)

        assert result.scan_detected is True
        assert result.recommended_strategy == "ocr-pageindex-local"
        assert result.selected_strategy == "ocr-pageindex-local"

    def test_long_pdf_strategy_override_wins_over_recommendation(self, kb_dir, tmp_path):
        src = tmp_path / "scan-override.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")
        prepared_pages = tmp_path / "prepared-pages.json"
        prepared_pages.write_text("[]", encoding="utf-8")
        prepared_md = tmp_path / "prepared-input.md"
        prepared_md.write_text("## Page 1", encoding="utf-8")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=False),
            patch("openkb.converter.is_probably_scanned_pdf", return_value=True),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": prepared_pages,
                    "pageindex_input_path": prepared_md,
                },
            ),
            patch("openkb.converter.convert_pdf_to_pages", return_value=[]),
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir, strategy_override="ocr-pageindex-local")

        assert result.recommended_strategy == "ocr-pageindex-local"
        assert result.selected_strategy == "ocr-pageindex-local"

    def test_force_ocr_long_pdf_reruns_ocr_artifact_preparation(self, kb_dir, tmp_path):
        src = tmp_path / "scan-force.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")
        prepared_pages = tmp_path / "prepared-pages.json"
        prepared_pages.write_text("[]", encoding="utf-8")
        prepared_md = tmp_path / "prepared-input.md"
        prepared_md.write_text("## Page 1", encoding="utf-8")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=False),
            patch("openkb.converter.is_probably_scanned_pdf", return_value=True),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": prepared_pages,
                    "pageindex_input_path": prepared_md,
                },
            ) as mock_prepare,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            convert_document(src, kb_dir, force=True, strategy_override="ocr-local-long")

        mock_prepare.assert_called_once()
        assert mock_prepare.call_args.kwargs["force"] is True

    def test_long_pdf_uses_ocr_artifacts_when_ocr_local_long_selected(self, kb_dir, tmp_path):
        src = tmp_path / "scan-ocr.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")
        ocr_pages_path = tmp_path / "ocr-pages.json"
        ocr_pages_path.write_text(
            json.dumps([{"page": 1, "content": "OCR page one", "images": []}], ensure_ascii=False),
            encoding="utf-8",
        )
        ocr_md_path = tmp_path / "ocr-input.md"
        ocr_md_path.write_text("## Page 1\n\nOCR page one", encoding="utf-8")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=False),
            patch("openkb.converter.is_probably_scanned_pdf", return_value=True),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": ocr_pages_path,
                    "pageindex_input_path": ocr_md_path,
                },
            ) as mock_prepare,
            patch("openkb.converter.convert_pdf_to_pages") as mock_pages,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir, strategy_override="ocr-local-long")

        mock_prepare.assert_called_once()
        mock_pages.assert_not_called()
        assert result.local_long_doc is True
        assert result.source_path is not None
        assert "OCR page one" in result.source_path.read_text(encoding="utf-8")

    def test_long_pdf_passes_job_to_ocr_artifact_preparation(self, kb_dir, tmp_path):
        src = tmp_path / "scan-job.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")
        ocr_pages_path = tmp_path / "ocr-pages.json"
        ocr_pages_path.write_text(
            json.dumps([{"page": 1, "content": "OCR page one", "images": []}], ensure_ascii=False),
            encoding="utf-8",
        )
        ocr_md_path = tmp_path / "ocr-input.md"
        ocr_md_path.write_text("## Page 1\n\nOCR page one", encoding="utf-8")
        job = object()

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=False),
            patch("openkb.converter.is_probably_scanned_pdf", return_value=True),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": ocr_pages_path,
                    "pageindex_input_path": ocr_md_path,
                },
            ) as mock_prepare,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            convert_document(src, kb_dir, strategy_override="ocr-local-long", job=job)

        assert mock_prepare.call_args.kwargs["job"] is job

    def test_ocr_pageindex_local_prepares_ocr_pages_and_pageindex_input(self, kb_dir, tmp_path):
        src = tmp_path / "scan-pageindex.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")
        ocr_pages_path = tmp_path / "ocr-pages.json"
        ocr_pages_path.write_text(
            json.dumps([{"page": 1, "content": "OCR PageIndex page", "images": []}], ensure_ascii=False),
            encoding="utf-8",
        )
        ocr_md_path = tmp_path / "ocr-input.md"
        ocr_md_path.write_text("## Page 1\n\nOCR PageIndex page", encoding="utf-8")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter._pageindex_long_doc_available", return_value=False),
            patch("openkb.converter.is_probably_scanned_pdf", return_value=True),
            patch(
                "openkb.converter.prepare_ocr_artifacts",
                return_value={
                    "pages_path": ocr_pages_path,
                    "pageindex_input_path": ocr_md_path,
                },
            ),
            patch("openkb.converter.convert_pdf_to_pages") as mock_pages,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir, strategy_override="ocr-pageindex-local")

        mock_pages.assert_not_called()
        assert result.is_long_doc is True
        assert result.local_long_doc is False
        assert result.selected_strategy == "ocr-pageindex-local"
        assert result.recommended_strategy == "ocr-pageindex-local"
        assert result.pageindex_input_path == ocr_md_path
        assert result.source_path is not None
        assert "OCR PageIndex page" in result.source_path.read_text(encoding="utf-8")
