from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from openkb.client.kb import get_document_data
from openkb.document_ledger import document_ledger_path, load_document_ledger
from openkb.source_relations import backfill_source_ingest_dates, get_source_documents


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "backfill_source_ingest_dates.py"
LEDGER_SCRIPT = REPO_ROOT / "scripts" / "backfill_document_ledger.py"


def _make_kb(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "kb"
    (kb_dir / "raw").mkdir(parents=True)
    (kb_dir / "wiki" / "sources").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")
    (kb_dir / ".openkb").mkdir(parents=True)
    (kb_dir / ".openkb" / "config.yaml").write_text("model: gpt-5.4-mini\n", encoding="utf-8")
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "hash-paper": {"name": "paper.pdf", "type": "pdf"},
                "hash-manual": {
                    "name": "manual.pdf",
                    "type": "pdf",
                    "ingested_at": "2026-05-08T14:15:00+08:00",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text(
        "---\nfull_text: sources/paper.md\n---\n\n# Paper\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "sources" / "paper.md").write_text("# full text\n", encoding="utf-8")
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"%PDF")
    (kb_dir / "raw" / "manual.pdf").write_bytes(b"%PDF")
    return kb_dir


def test_get_source_documents_resolves_ingested_date_from_log(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "log.md").write_text(
        "# Operations Log\n\n"
        "## [2026-05-10 09:15:00] ingest | paper.pdf\n\n"
        "## [2026-05-09 08:00:00] ingest | paper.pdf\n\n",
        encoding="utf-8",
    )

    documents = get_source_documents(kb_dir)
    paper = next(item for item in documents if item["name"] == "paper.pdf")
    manual = next(item for item in documents if item["name"] == "manual.pdf")

    assert paper["ingested_at"] == "2026-05-10T09:15:00+08:00"
    assert paper["ingested_date"] == "2026-05-10"
    assert manual["ingested_at"] == "2026-05-08T14:15:00+08:00"
    assert manual["ingested_date"] == "2026-05-08"


def test_get_document_data_exposes_ingested_date_fields(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "log.md").write_text(
        "# Operations Log\n\n"
        "## [2026-05-10 09:15:00] ingest | paper.pdf\n\n",
        encoding="utf-8",
    )

    data = get_document_data(kb_dir)
    paper = next(item for item in data["documents"] if item["name"] == "paper.pdf")

    assert paper["ingested_at"] == "2026-05-10T09:15:00+08:00"
    assert paper["ingested_date"] == "2026-05-10"


def test_backfill_source_ingest_dates_persists_missing_metadata(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "log.md").write_text(
        "# Operations Log\n\n"
        "## [2026-05-10 09:15:00] ingest | paper.pdf\n\n",
        encoding="utf-8",
    )

    result = backfill_source_ingest_dates(kb_dir)

    assert result["updated"] == 1
    assert result["skipped"] == 1
    hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
    assert hashes["hash-paper"]["ingested_at"] == "2026-05-10T09:15:00+08:00"
    assert hashes["hash-manual"]["ingested_at"] == "2026-05-08T14:15:00+08:00"


def test_backfill_script_updates_kb_hashes(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "log.md").write_text(
        "# Operations Log\n\n"
        "## [2026-05-10 09:15:00] ingest | paper.pdf\n\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(kb_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "updated=1" in result.stdout
    hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
    assert hashes["hash-paper"]["ingested_at"] == "2026-05-10T09:15:00+08:00"


def test_backfill_document_ledger_script_persists_legacy_records(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    result = subprocess.run(
        [sys.executable, str(LEDGER_SCRIPT), str(kb_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "added=2" in result.stdout
    assert document_ledger_path(kb_dir).exists()
    ledger = load_document_ledger(kb_dir)
    assert sorted(ledger["documents"]) == ["hash-manual", "hash-paper"]
    assert ledger["documents"]["hash-paper"]["workflow_state"]["summary_state"] == "ready"
