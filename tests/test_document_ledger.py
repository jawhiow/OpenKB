from __future__ import annotations

import json
from pathlib import Path

from openkb.document_ledger import (
    backfill_document_ledger,
    build_document_ledger_record,
    delete_document_ledger_record,
    document_ledger_path,
    empty_document_ledger,
    get_document_ledger_record,
    list_effective_document_ledger_records,
    load_document_ledger,
    save_document_ledger,
    select_document_ledger_records,
    update_document_workflow_state,
    upsert_document_ledger_record,
)


def _make_kb(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "kb"
    (kb_dir / ".openkb").mkdir(parents=True)
    return kb_dir


def test_load_document_ledger_returns_empty_payload_when_missing(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    assert load_document_ledger(kb_dir) == empty_document_ledger()


def test_save_and_load_document_ledger_normalize_records(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    save_document_ledger(
        kb_dir,
        {
            "version": 999,
            "documents": {
                "hash-a": {
                    "name": " paper.pdf ",
                    "stem": "paper",
                    "raw_path": "raw/paper.pdf",
                    "workflow_state": {"summary_state": "ready"},
                    "review": {"summary_score": "88"},
                    "execution": {"retry_count": "2"},
                }
            },
        },
    )

    loaded = load_document_ledger(kb_dir)
    assert loaded["version"] == 1
    assert loaded["documents"]["hash-a"] == {
        "file_hash": "hash-a",
        "name": "paper.pdf",
        "stem": "paper",
        "raw_path": "raw/paper.pdf",
        "ingested_at": None,
        "source_kind": "",
        "page_count": None,
        "scan_detected": False,
        "workflow_state": {
            "ingest_state": "imported",
            "ocr_state": "not_needed",
            "source_state": "queued",
            "summary_state": "ready",
            "review_state": "unreviewed",
            "promotion_state": "not_selected",
        },
        "review": {
            "ingest_score": None,
            "summary_score": 88,
            "promotion_score": None,
            "review_notes": "",
            "recommended_ingest_mode": "",
            "approved_by": "",
            "approved_at": None,
        },
        "execution": {
            "last_error": "",
            "retry_count": 2,
            "updated_at": None,
        },
    }


def test_upsert_and_get_document_ledger_record_merge_defaults_and_updates(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    created = upsert_document_ledger_record(
        kb_dir,
        "hash-a",
        {
            "workflow_state": {"source_state": "ready", "summary_state": "queued"},
            "review": {"recommended_ingest_mode": "summary_only"},
        },
        defaults={
            "name": "paper.pdf",
            "stem": "paper",
            "raw_path": "raw/paper.pdf",
            "source_kind": "markdown",
            "page_count": 12,
        },
    )

    assert created["name"] == "paper.pdf"
    assert created["source_kind"] == "markdown"
    assert created["page_count"] == 12
    assert created["workflow_state"]["source_state"] == "ready"
    assert created["workflow_state"]["summary_state"] == "queued"
    assert created["review"]["recommended_ingest_mode"] == "summary_only"

    found = get_document_ledger_record(kb_dir, "hash-a")
    assert found == created


def test_delete_document_ledger_record_removes_existing_record(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    upsert_document_ledger_record(kb_dir, "hash-a", {"name": "paper.pdf"})

    removed = delete_document_ledger_record(kb_dir, "hash-a")

    assert removed is not None
    assert removed["file_hash"] == "hash-a"
    assert removed["name"] == "paper.pdf"
    assert get_document_ledger_record(kb_dir, "hash-a") is None
    assert delete_document_ledger_record(kb_dir, "hash-a") is None


def test_update_document_workflow_state_and_select_filters_records(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    upsert_document_ledger_record(
        kb_dir,
        "hash-a",
        build_document_ledger_record(
            "hash-a",
            defaults={
                "name": "paper.pdf",
                "workflow_state": {
                    "summary_state": "ready",
                    "review_state": "approved",
                    "promotion_state": "promoted",
                },
            },
        ),
    )
    upsert_document_ledger_record(
        kb_dir,
        "hash-b",
        {
            "name": "manual.pdf",
            "workflow_state": {
                "summary_state": "not_started",
                "review_state": "unreviewed",
                "promotion_state": "not_selected",
            },
        },
    )

    updated = update_document_workflow_state(kb_dir, "hash-b", {"summary_state": "queued"})
    assert updated["workflow_state"]["summary_state"] == "queued"

    approved = select_document_ledger_records(kb_dir, review_state="approved")
    queued = select_document_ledger_records(kb_dir, file_hashes=["hash-b"], summary_state="queued")

    assert [record["file_hash"] for record in approved] == ["hash-a"]
    assert [record["file_hash"] for record in queued] == ["hash-b"]

    payload = json.loads(document_ledger_path(kb_dir).read_text(encoding="utf-8"))
    assert sorted(payload["documents"]) == ["hash-a", "hash-b"]


def test_list_effective_document_ledger_records_merges_inferred_hash_documents(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "raw").mkdir(parents=True)
    (kb_dir / "wiki").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "hash-a": {"name": "paper.pdf", "type": "pdf"},
                "hash-b": {"name": "manual.pdf", "type": "long_pdf"},
            }
        ),
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text("# Paper\n", encoding="utf-8")

    records = list_effective_document_ledger_records(kb_dir)

    assert records["hash-a"]["workflow_state"]["summary_state"] == "ready"
    assert records["hash-a"]["workflow_state"]["promotion_state"] == "not_selected"
    assert records["hash-a"]["workflow_state"]["review_state"] == "approved"
    assert records["hash-b"]["workflow_state"]["summary_state"] == "not_started"
    assert records["hash-b"]["workflow_state"]["review_state"] == "unreviewed"


def test_backfill_document_ledger_persists_effective_records(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "raw").mkdir(parents=True)
    (kb_dir / "wiki").mkdir(parents=True)
    (kb_dir / "wiki" / "sources").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"hash-a": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "sources" / "paper.md").write_text("# Source\n", encoding="utf-8")

    result = backfill_document_ledger(kb_dir)

    assert result == {"added": 1, "updated": 0, "unchanged": 0, "total": 1}
    record = get_document_ledger_record(kb_dir, "hash-a")
    assert record is not None
    assert record["workflow_state"]["source_state"] == "ready"
    assert record["workflow_state"]["summary_state"] == "not_started"
