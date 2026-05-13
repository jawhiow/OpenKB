from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openkb.document_ledger import get_document_ledger_record, upsert_document_ledger_record
from openkb.workflows.promotion_pipeline import promote_summary_document, promote_summary_documents


def _add_reviewed_summary(kb_dir: Path, *, review_state: str = "approved") -> None:
    (kb_dir / "wiki" / "sources" / "report.md").write_text("# Report\n\nSource text.", encoding="utf-8")
    review_summary_path = kb_dir / ".openkb" / "review_summaries" / "2026-05-10" / "report.md"
    review_summary_path.parent.mkdir(parents=True, exist_ok=True)
    review_summary_path.write_text(
        "---\ndoc_type: short\nfull_text: sources/report.md\n---\n\n# Summary",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"hash-report": {"name": "report.md", "type": "md"}}),
        encoding="utf-8",
    )
    upsert_document_ledger_record(
        kb_dir,
        "hash-report",
        {
            "name": "report.md",
            "stem": "report",
            "raw_path": "raw/report.md",
            "ingested_at": "2026-05-10T09:30:00+08:00",
            "review_summary_path": "review_summaries/2026-05-10/report.md",
            "source_kind": "markdown",
            "workflow_state": {
                "source_state": "ready",
                "summary_state": "ready",
                "review_state": review_state,
                "promotion_state": "not_selected",
            },
        },
    )


def test_promote_summary_document_requires_approval(kb_dir: Path):
    _add_reviewed_summary(kb_dir, review_state="held")

    with pytest.raises(RuntimeError, match="not approved"):
        promote_summary_document(kb_dir, "hash-report")

    record = get_document_ledger_record(kb_dir, "hash-report")
    assert record["workflow_state"]["promotion_state"] == "not_selected"


def test_promote_summary_document_updates_promotion_state(kb_dir: Path):
    _add_reviewed_summary(kb_dir, review_state="approved")

    with patch("openkb.workflows.promotion_pipeline._compile_concepts", new_callable=AsyncMock) as mock_compile:
        result = promote_summary_document(kb_dir, "hash-report", model="gpt-test")

    assert result["skipped"] is False
    assert mock_compile.await_count == 1
    assert (kb_dir / "wiki" / "summaries" / "report.md").exists()
    record = get_document_ledger_record(kb_dir, "hash-report")
    assert record["workflow_state"]["promotion_state"] == "promoted"
    assert record["execution"]["last_error"] == ""


def test_promote_summary_documents_selects_approved_records(kb_dir: Path):
    _add_reviewed_summary(kb_dir, review_state="approved")

    with patch("openkb.workflows.promotion_pipeline._compile_concepts", new_callable=AsyncMock):
        result = promote_summary_documents(kb_dir)

    assert result["promoted"] == 1
    assert result["failed"] == 0
    assert result["total"] == 1


def test_promote_summary_document_supports_legacy_kb_without_persisted_ledger(kb_dir: Path):
    (kb_dir / "wiki" / "summaries" / "report.md").write_text(
        "---\nfull_text: sources/report.md\n---\n\n# Summary",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"hash-report": {"name": "report.md", "type": "md"}}),
        encoding="utf-8",
    )

    with patch("openkb.workflows.promotion_pipeline._compile_concepts", new_callable=AsyncMock) as mock_compile:
        result = promote_summary_document(kb_dir, "hash-report", model="gpt-test")

    assert result["skipped"] is False
    assert mock_compile.await_count == 1
    record = get_document_ledger_record(kb_dir, "hash-report")
    assert record is not None
    assert record["workflow_state"]["promotion_state"] == "promoted"
