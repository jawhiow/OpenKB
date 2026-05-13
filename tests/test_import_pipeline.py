from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from unittest.mock import MagicMock

from openkb.document_ledger import get_document_ledger_record
from openkb.client.kb import get_document_data
from openkb.state import HashRegistry
from openkb.workflows.import_pipeline import import_document_source


def test_import_document_source_registers_source_without_summary(kb_dir: Path):
    src = kb_dir / "incoming" / "report.md"
    src.parent.mkdir()
    src.write_text("# Report\n\nSource only.", encoding="utf-8")

    result = import_document_source(src, kb_dir)

    assert result["skipped"] is False
    assert result["raw_path"].startswith(".openkb/raw/")
    assert result["raw_path"].endswith("/report.md")
    assert result["source_path"].startswith(".openkb/sources/")
    assert result["source_path"].endswith("/report.md")
    assert (kb_dir / result["raw_path"]).exists()
    assert (kb_dir / result["source_path"]).exists()
    assert not (kb_dir / "wiki" / "summaries" / "report.md").exists()

    hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
    file_hash = next(iter(hashes))
    assert hashes[file_hash]["name"] == "report.md"
    assert hashes[file_hash]["type"] == "md"

    document = get_document_data(kb_dir)["documents"][0]
    assert document["hash"] == file_hash
    assert document["source_path"] == result["source_path"]
    assert document["summary_exists"] is False
    assert document["workflow_state"]["source_state"] == "ready"
    assert document["workflow_state"]["summary_state"] == "not_started"
    assert document["workflow_state"]["promotion_state"] == "not_selected"


def test_import_document_source_records_failed_source_state(kb_dir: Path):
    src = kb_dir / "incoming" / "broken.md"
    src.parent.mkdir()
    src.write_text("# Broken", encoding="utf-8")
    file_hash = HashRegistry.hash_file(src)

    with patch("openkb.workflows.import_pipeline.convert_document", side_effect=RuntimeError("conversion exploded")):
        with pytest.raises(RuntimeError, match="conversion exploded"):
            import_document_source(src, kb_dir)

    record = get_document_ledger_record(kb_dir, file_hash)
    assert record is not None
    assert record["name"] == "broken.md"
    assert record["workflow_state"]["source_state"] == "failed"
    assert record["workflow_state"]["summary_state"] == "not_started"
    assert record["execution"]["last_error"] == "conversion exploded"
    assert record["execution"]["retry_count"] == 1
    document = get_document_data(kb_dir)["documents"][0]
    assert document["hash"] == file_hash
    assert document["workflow_state"]["source_state"] == "failed"
    assert document["execution"]["last_error"] == "conversion exploded"


def test_import_document_source_records_ocr_pdf_as_source_ready(kb_dir: Path, tmp_path: Path):
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"%PDF-1.4 fake scanned content")
    ocr_pages_path = tmp_path / "ocr-pages.json"
    ocr_pages_path.write_text(
        json.dumps([{"page": 1, "content": "OCR page text", "images": []}], ensure_ascii=False),
        encoding="utf-8",
    )
    ocr_md_path = tmp_path / "ocr-input.md"
    ocr_md_path.write_text("## Page 1\n\nOCR page text", encoding="utf-8")

    fake_doc = MagicMock()
    fake_doc.page_count = 5
    fake_doc.__enter__ = MagicMock(return_value=fake_doc)
    fake_doc.__exit__ = MagicMock(return_value=False)

    with patch("openkb.converter.pymupdf.open", return_value=fake_doc), \
         patch("openkb.converter.is_probably_scanned_pdf", return_value=True), \
         patch(
             "openkb.converter.prepare_ocr_artifacts",
             return_value={"pages_path": ocr_pages_path, "pageindex_input_path": ocr_md_path},
         ):
        result = import_document_source(src, kb_dir)

    record = result["ledger_record"]
    assert result["skipped"] is False
    assert result["source_path"].startswith(".openkb/sources/")
    assert result["source_path"].endswith("/scan.json")
    assert record["source_kind"] == "page_json"
    assert record["scan_detected"] is True
    assert record["workflow_state"]["ocr_state"] == "ready"
    assert record["workflow_state"]["source_state"] == "ready"
    assert record["workflow_state"]["summary_state"] == "not_started"
    assert (kb_dir / result["source_path"]).exists()
    assert not (kb_dir / "wiki" / "summaries" / "scan.md").exists()
