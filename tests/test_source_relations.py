from __future__ import annotations

import json
from pathlib import Path

import pytest

from openkb.source_relations import (
    delete_source_document,
    get_source_document_detail,
    get_source_documents,
    resolve_source_document,
)


def _make_kb(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "kb"
    (kb_dir / "raw").mkdir(parents=True)
    for subdir in (
        "sources/images/paper",
        "summaries",
        "companies",
        "industries",
        "themes",
        "metrics",
        "risks",
        "concepts",
    ):
        (kb_dir / "wiki" / subdir).mkdir(parents=True)
    (kb_dir / ".openkb").mkdir()
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "hash-paper": {"name": "paper.pdf", "type": "pdf", "pages": 12},
                "hash-other": {"name": "other.pdf", "type": "pdf"},
            }
        ),
        encoding="utf-8",
    )
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"%PDF")
    (kb_dir / "wiki" / "sources" / "paper.md").write_text("# Full text", encoding="utf-8")
    (kb_dir / "wiki" / "sources" / "images" / "paper" / "chart.png").write_bytes(b"png")
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text(
        "---\ndoc_type: short\nfull_text: sources/paper.md\n---\n\n# Paper",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "summaries" / "other.md").write_text(
        "---\ndoc_type: short\nfull_text: sources/other.md\n---\n\n# Other",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "companies" / "TSMC.md").write_text(
        "---\nsources: [summaries/paper.md]\nbrief: AI foundry\n---\n\n# TSMC\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "HBM.md").write_text(
        "---\nsources: [summaries/paper.md]\nbrief: Memory bottleneck\n---\n\n# HBM\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "Shared.md").write_text(
        "---\nsources: [summaries/other.md, summaries/paper.md]\nbrief: Shared idea\n---\n\n"
        "# Shared\n\n## Related Documents\n- [[summaries/other]]\n- [[summaries/paper]]\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n"
        "## Documents\n"
        "- [[summaries/paper]] (short) - Paper\n"
        "- [[summaries/other]] (short) - Other\n\n"
        "## Companies\n"
        "- [[companies/TSMC]] - AI foundry\n\n"
        "## Concepts\n"
        "- [[concepts/HBM]] - Memory bottleneck\n"
        "- [[concepts/Shared]] - Shared idea\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "evidence_map.json").write_text(
        json.dumps(
            {
                "summaries/paper.md": [
                    {"source": "sources/paper.md", "summary": "summaries/paper", "page": "3", "snippet": "Paper evidence"}
                ],
                "companies/TSMC.md": [
                    {"source": "summaries/paper.md", "summary": "summaries/paper", "page": "7", "snippet": "TSMC evidence"}
                ],
                "concepts/Shared.md": [
                    {"source": "summaries/other.md", "summary": "summaries/other", "page": "1", "snippet": "Other evidence"},
                    {"source": "summaries/paper.md", "summary": "summaries/paper", "page": "2", "snippet": "Paper shared evidence"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return kb_dir


def test_get_source_documents_includes_related_pages_grouped_by_type(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    data = get_source_documents(kb_dir)

    paper = next(item for item in data if item["name"] == "paper.pdf")
    assert paper["hash"] == "hash-paper"
    assert paper["stem"] == "paper"
    assert paper["related_count"] == 4
    assert paper["related_pages"]["summaries"] == [
        {"path": "summaries/paper.md", "page": "summaries/paper", "title": "paper", "shared": False}
    ]
    assert paper["related_pages"]["companies"][0]["path"] == "companies/TSMC.md"
    assert paper["related_pages"]["concepts"] == [
        {"path": "concepts/HBM.md", "page": "concepts/HBM", "title": "HBM", "shared": False},
        {"path": "concepts/Shared.md", "page": "concepts/Shared", "title": "Shared", "shared": True},
    ]


def test_resolve_source_document_accepts_hash_prefix_name_or_stem(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    assert resolve_source_document(kb_dir, "hash-p")["hash"] == "hash-paper"
    assert resolve_source_document(kb_dir, "paper.pdf")["hash"] == "hash-paper"
    assert resolve_source_document(kb_dir, "paper")["hash"] == "hash-paper"

    with pytest.raises(ValueError, match="No indexed source document"):
        resolve_source_document(kb_dir, "missing")


def test_get_source_document_detail_returns_one_document(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    detail = get_source_document_detail(kb_dir, "paper")

    assert detail["name"] == "paper.pdf"
    assert detail["related_count"] == 4
    assert detail["source_summary"] == "summaries/paper.md"


def test_delete_source_document_removes_owned_artifacts_and_keeps_shared_pages(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    result = delete_source_document(kb_dir, "paper")

    assert result["document"]["name"] == "paper.pdf"
    assert result["removed_pages"] == [
        "summaries/paper.md",
        "companies/TSMC.md",
        "concepts/HBM.md",
    ]
    assert result["updated_pages"] == ["concepts/Shared.md"]
    assert "raw/paper.pdf" in result["removed_files"]
    assert "wiki/sources/paper.md" in result["removed_files"]
    assert "wiki/sources/images/paper/" in result["removed_files"]

    assert not (kb_dir / "raw" / "paper.pdf").exists()
    assert not (kb_dir / "wiki" / "sources" / "paper.md").exists()
    assert not (kb_dir / "wiki" / "sources" / "images" / "paper").exists()
    assert not (kb_dir / "wiki" / "summaries" / "paper.md").exists()
    assert not (kb_dir / "wiki" / "companies" / "TSMC.md").exists()
    assert not (kb_dir / "wiki" / "concepts" / "HBM.md").exists()

    shared = (kb_dir / "wiki" / "concepts" / "Shared.md").read_text(encoding="utf-8")
    assert "sources: [summaries/other.md]" in shared
    assert "[[summaries/paper]]" not in shared
    assert "[[summaries/other]]" in shared

    index = (kb_dir / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[summaries/paper]]" not in index
    assert "[[companies/TSMC]]" not in index
    assert "[[concepts/HBM]]" not in index
    assert "[[concepts/Shared]]" in index

    evidence = json.loads((kb_dir / "wiki" / "evidence_map.json").read_text(encoding="utf-8"))
    assert "summaries/paper.md" not in evidence
    assert "companies/TSMC.md" not in evidence
    assert evidence["concepts/Shared.md"] == [
        {"source": "summaries/other.md", "summary": "summaries/other", "page": "1", "snippet": "Other evidence"}
    ]

    hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
    assert "hash-paper" not in hashes
    assert "hash-other" in hashes
