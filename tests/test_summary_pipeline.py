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
        "scorecard": {
            "method": "llm_summary_value_v1",
            "overall_assessment": "High-signal and concrete.",
            "total_score": 84,
            "dimensions": {
                "source_coverage": {"score": 21, "reason": "captures major sections"},
                "factual_density": {"score": 16, "reason": "includes key numbers"},
                "structure_clarity": {"score": 12, "reason": "easy to scan"},
                "retrieval_value": {"score": 17, "reason": "good future recall value"},
                "actionability": {"score": 8, "reason": "preserves decisions"},
                "cross_linking": {"score": 10, "reason": "durable concept hooks"},
            },
        },
    })

    with patch("openkb.workflows.summary_pipeline._llm_call", return_value=summary_response) as mock_llm:
        result = summarize_document_source(kb_dir, "hash-report", model="gpt-test")

    assert result["skipped"] is False
    assert result["summary_path"] == "review_summaries/report.md" or result["summary_path"].endswith("/report.md")
    summary_path = kb_dir / ".openkb" / result["summary_path"]
    assert summary_path.exists()
    assert "full_text: sources/report.md" in summary_path.read_text(encoding="utf-8")
    assert not (kb_dir / "wiki" / "summaries" / "report.md").exists()
    generated_pages = [
        page
        for directory in ("companies", "industries", "concepts")
        for page in (kb_dir / "wiki" / directory).glob("*.md")
    ]
    assert generated_pages == []
    record = get_document_ledger_record(kb_dir, "hash-report")
    assert str(record["review_summary_path"]).endswith("/report.md")
    assert record["workflow_state"]["summary_state"] == "ready"
    assert record["workflow_state"]["review_state"] == "unreviewed"
    assert record["workflow_state"]["promotion_state"] == "not_selected"
    assert record["review"]["summary_score"] == 84
    assert record["review"]["summary_score_source"] == "auto"
    assert record["review"]["summary_scorecard"]["dimensions"]["factual_density"]["score"] == 16
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


def test_summarize_documents_reports_progress_and_runs_workers(kb_dir: Path):
    _add_source_doc(kb_dir, file_hash="hash-a")
    _add_source_doc(kb_dir, file_hash="hash-b")
    events: list[dict] = []
    seen: list[str] = []

    def worker(file_hash: str) -> dict:
        seen.append(file_hash)
        return {
            "file_hash": file_hash,
            "name": f"{file_hash}.md",
            "skipped": False,
            "summary_path": f"review_summaries/{file_hash}.md",
        }

    result = summarize_documents(
        kb_dir,
        file_hashes=["hash-a", "hash-b"],
        max_workers=2,
        worker=worker,
        progress_callback=events.append,
    )

    assert sorted(seen) == ["hash-a", "hash-b"]
    assert result["generated"] == 2
    assert result["failed"] == 0
    assert result["total"] == 2
    assert [event["event"] for event in events].count("start") == 2
    assert [event["event"] for event in events].count("generated") == 2
    assert events[-1]["completed"] == 2


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
    assert record["review"]["summary_score_source"] == "manual"
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
    assert record["review"]["summary_score_source"] == "manual"
    assert record["review"]["review_notes"] == "needs a second pass"


def test_summarize_document_source_falls_back_to_heuristic_scorecard_when_missing(kb_dir: Path):
    _add_source_doc(kb_dir)
    summary_response = json.dumps({
        "brief": "Revenue growth report.",
        "content": "# Report Summary\n\n- Revenue grew 20%\n- Margin improved to 35%\n- [[concepts/pricing_power]] remains relevant",
    })

    with patch("openkb.workflows.summary_pipeline._llm_call", return_value=summary_response):
        summarize_document_source(kb_dir, "hash-report", model="gpt-test")

    record = get_document_ledger_record(kb_dir, "hash-report")
    assert record is not None
    assert record["review"]["summary_score_source"] == "auto"
    assert isinstance(record["review"]["summary_score"], int)
    assert record["review"]["summary_scorecard"]["method"] == "heuristic_summary_value_v1"
    assert record["review"]["summary_scorecard"]["total_score"] == record["review"]["summary_score"]
