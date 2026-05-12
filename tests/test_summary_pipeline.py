from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from openkb.document_ledger import get_document_ledger_record, upsert_document_ledger_record
from openkb.workflows.summary_pipeline import (
    summarize_document_source,
    summarize_documents,
    update_summary_review,
    update_summary_reviews,
)


def _add_source_doc(kb_dir: Path, *, file_hash: str = "hash-report") -> None:
    (kb_dir / "wiki" / "sources" / "report.md").write_text(
        "# Report\n\nRevenue grew 20%.",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({file_hash: {"name": "report.md", "type": "md"}}),
        encoding="utf-8",
    )
    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "name": "report.md",
            "stem": "report",
            "raw_path": "raw/report.md",
            "source_kind": "markdown",
            "workflow_state": {
                "source_state": "ready",
                "summary_state": "not_started",
                "review_state": "unreviewed",
                "promotion_state": "not_selected",
            },
        },
    )


def test_summarize_document_source_writes_summary_only(kb_dir: Path):
    _add_source_doc(kb_dir)
    summary_response = json.dumps({
        "brief": "Revenue growth report.",
        "content": "# Report Summary\n\nRevenue grew 20%.",
    })

    with patch("openkb.workflows.summary_pipeline._llm_call", return_value=summary_response) as mock_llm:
        result = summarize_document_source(kb_dir, "hash-report", model="gpt-test")

    assert result["skipped"] is False
    assert result["summary_path"] == "summaries/report.md"
    summary_path = kb_dir / "wiki" / "summaries" / "report.md"
    assert summary_path.exists()
    assert "full_text: sources/report.md" in summary_path.read_text(encoding="utf-8")
    generated_pages = [
        page
        for directory in ("companies", "industries", "concepts")
        for page in (kb_dir / "wiki" / directory).glob("*.md")
    ]
    assert generated_pages == []
    record = get_document_ledger_record(kb_dir, "hash-report")
    assert record["workflow_state"]["summary_state"] == "ready"
    assert record["workflow_state"]["review_state"] == "unreviewed"
    assert record["workflow_state"]["promotion_state"] == "not_selected"
    mock_llm.assert_called_once()


def test_summarize_documents_batches_source_ready_records(kb_dir: Path):
    _add_source_doc(kb_dir)

    with patch(
        "openkb.workflows.summary_pipeline._llm_call",
        return_value=json.dumps({"content": "# Summary"}),
    ):
        result = summarize_documents(kb_dir)

    assert result["generated"] == 1
    assert result["failed"] == 0
    assert result["total"] == 1


def test_summarize_documents_selects_legacy_kb_without_persisted_ledger(kb_dir: Path):
    (kb_dir / "wiki" / "sources" / "report.md").write_text(
        "# Report\n\nRevenue grew 20%.",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"hash-report": {"name": "report.md", "type": "md"}}),
        encoding="utf-8",
    )

    with patch(
        "openkb.workflows.summary_pipeline._llm_call",
        return_value=json.dumps({"content": "# Summary"}),
    ):
        result = summarize_documents(kb_dir)

    assert result["generated"] == 1
    record = get_document_ledger_record(kb_dir, "hash-report")
    assert record is not None
    assert record["workflow_state"]["summary_state"] == "ready"


def test_update_summary_review_sets_score_and_approval(kb_dir: Path):
    _add_source_doc(kb_dir)

    record = update_summary_review(
        kb_dir,
        "hash-report",
        review_state="approved",
        summary_score=86,
        review_notes="good enough",
        approved_by="alice",
    )

    assert record["workflow_state"]["review_state"] == "approved"
    assert record["review"]["summary_score"] == 86
    assert record["review"]["review_notes"] == "good enough"
    assert record["review"]["approved_by"] == "alice"
    assert record["review"]["approved_at"]


def test_update_summary_reviews_batches_review_metadata(kb_dir: Path):
    _add_source_doc(kb_dir)

    result = update_summary_reviews(
        kb_dir,
        [
            {
                "file_hash": "hash-report",
                "review_state": "held",
                "summary_score": "74",
                "review_notes": "needs a second pass",
            }
        ],
    )

    assert result["updated"] == 1
    record = get_document_ledger_record(kb_dir, "hash-report")
    assert record["workflow_state"]["review_state"] == "held"
    assert record["review"]["summary_score"] == 74
    assert record["review"]["review_notes"] == "needs a second pass"
