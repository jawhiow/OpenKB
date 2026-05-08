"""Structured evidence-map helpers for generated wiki pages."""
from __future__ import annotations

import json
from pathlib import Path


def update_evidence_map(
    wiki_root: Path,
    page_path: str,
    evidence: list[dict[str, str]],
    *,
    merge_sources: bool = False,
) -> None:
    """Write structured evidence entries for a wiki page.

    ``page_path`` is stored relative to the wiki root, such as
    ``concepts/HBM.md``. Empty evidence lists are ignored so callers can use
    this only when they have source-backed snippets. When ``merge_sources`` is
    true, evidence for other sources on the same page is preserved while
    entries for the current source are replaced.
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

    new_entries = [
        {
            "source": item["source"],
            "summary": item["link"],
            "page": item.get("page", ""),
            "snippet": item["snippet"],
        }
        for item in evidence
    ]
    if merge_sources:
        new_sources = {entry["source"] for entry in new_entries}
        new_summaries = {entry["summary"] for entry in new_entries}
        old_entries = existing.get(page_path, [])
        if not isinstance(old_entries, list):
            old_entries = []
        kept_entries = [
            entry for entry in old_entries
            if isinstance(entry, dict)
            and entry.get("source") not in new_sources
            and entry.get("summary") not in new_summaries
        ]
        seen: set[tuple[str, str, str, str]] = set()
        merged: list[dict[str, str]] = []
        for entry in [*kept_entries, *new_entries]:
            key = (
                str(entry.get("source", "")),
                str(entry.get("summary", "")),
                str(entry.get("page", "")),
                str(entry.get("snippet", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "source": str(entry.get("source", "")),
                "summary": str(entry.get("summary", "")),
                "page": str(entry.get("page", "")),
                "snippet": str(entry.get("snippet", "")),
            })
        existing[page_path] = merged
    else:
        existing[page_path] = new_entries
    map_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
