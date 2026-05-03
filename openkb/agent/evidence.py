"""Structured evidence-map helpers for generated wiki pages."""
from __future__ import annotations

import json
from pathlib import Path


def update_evidence_map(wiki_root: Path, page_path: str, evidence: list[dict[str, str]]) -> None:
    """Write structured evidence entries for a wiki page.

    ``page_path`` is stored relative to the wiki root, such as
    ``concepts/HBM.md``. Empty evidence lists are ignored so callers can use
    this only when they have source-backed snippets.
    """
    if not evidence:
        return

    map_path = wiki_root / "evidence_map.json"
    try:
        existing = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}

    existing[page_path] = [
        {
            "source": item["source"],
            "summary": item["link"],
            "page": item.get("page", ""),
            "snippet": item["snippet"],
        }
        for item in evidence
    ]
    map_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
