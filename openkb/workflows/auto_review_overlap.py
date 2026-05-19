"""Overlap-score moat: deterministic semantic similarity against existing wiki pages.

Reuses the term-extraction helpers from :mod:`openkb.ingest_gate` to keep one
canonical implementation of the jaccard tokenizer. The result feeds the
auto-review decision engine as an objective novelty signal that complements
the LLM-reported ``novelty_vs_kb`` dimension.

A keyword cache lives at ``.openkb/concept_terms_cache.json`` and is keyed by
the per-file mtime+size. This keeps the overlap check fast even on KBs with
hundreds of concept pages.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from openkb.ingest_gate import _semantic_terms, _text_similarity


_DEFAULT_WIKI_DIRS: tuple[str, ...] = ("concepts", "companies", "industries")
_DEFAULT_TOP_K = 5
_DEFAULT_MIN_SCORE = 0.30
_CACHE_FILENAME = "concept_terms_cache.json"
_CACHE_VERSION = 1


@dataclass
class OverlapHit:
    """One matching wiki page with its similarity score."""
    page: str          # e.g. "concepts/HBM"
    score: float       # jaccard similarity in [0, 1]


@dataclass
class OverlapResult:
    """Result of overlap evaluation against the existing wiki."""
    max_overlap: float = 0.0
    top_hits: list[OverlapHit] = field(default_factory=list)
    scanned_pages: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_overlap": round(self.max_overlap, 4),
            "scanned_pages": self.scanned_pages,
            "top_hits": [{"page": hit.page, "score": round(hit.score, 4)} for hit in self.top_hits],
        }


def _cache_path(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / _CACHE_FILENAME


def _file_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _load_cache(kb_dir: Path) -> dict[str, Any]:
    path = _cache_path(kb_dir)
    if not path.exists():
        return {"version": _CACHE_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {"version": _CACHE_VERSION, "entries": {}}
    if data.get("version") != _CACHE_VERSION:
        return {"version": _CACHE_VERSION, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def _save_cache(kb_dir: Path, cache: Mapping[str, Any]) -> None:
    path = _cache_path(kb_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _candidate_pages(wiki_dir: Path, dirs: Iterable[str]) -> list[Path]:
    pages: list[Path] = []
    for name in dirs:
        directory = wiki_dir / name
        if not directory.exists():
            continue
        try:
            pages.extend(p for p in directory.glob("*.md") if p.is_file())
        except OSError:
            continue
    return pages


def _terms_for_page(page: Path, cache_entries: dict[str, Any]) -> set[str]:
    rel = f"{page.parent.name}/{page.name}"
    sig = _file_signature(page)
    cached = cache_entries.get(rel)
    if isinstance(cached, Mapping) and cached.get("signature") == sig:
        terms_raw = cached.get("terms")
        if isinstance(terms_raw, list):
            return {str(t) for t in terms_raw if isinstance(t, str)}
    try:
        text = page.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    terms = _semantic_terms(text)
    cache_entries[rel] = {"signature": sig, "terms": sorted(terms)}
    return terms


def evaluate_overlap(
    summary_text: str,
    kb_dir: Path,
    *,
    wiki_dirs: Iterable[str] = _DEFAULT_WIKI_DIRS,
    top_k: int = _DEFAULT_TOP_K,
    min_score: float = _DEFAULT_MIN_SCORE,
    refresh_cache: bool = False,
) -> OverlapResult:
    """Compute jaccard similarity between ``summary_text`` and existing wiki pages.

    Pages are taken from ``wiki/<dir>/*.md`` for each configured directory.
    Only hits with similarity ≥ ``min_score`` are kept in ``top_hits``.
    ``max_overlap`` is the highest similarity seen regardless of ``min_score``.
    """
    candidate_terms = _semantic_terms(str(summary_text or ""))
    wiki_dir = Path(kb_dir) / "wiki"
    pages = _candidate_pages(wiki_dir, wiki_dirs)

    if not candidate_terms or not pages:
        return OverlapResult(max_overlap=0.0, top_hits=[], scanned_pages=len(pages))

    cache = {"version": _CACHE_VERSION, "entries": {}} if refresh_cache else _load_cache(kb_dir)
    entries = cache.setdefault("entries", {})

    max_overlap = 0.0
    hits: list[OverlapHit] = []

    for page in pages:
        existing = _terms_for_page(page, entries)
        score = _text_similarity(candidate_terms, existing)
        if score > max_overlap:
            max_overlap = score
        if score >= min_score:
            page_id = f"{page.parent.name}/{page.stem}"
            hits.append(OverlapHit(page=page_id, score=score))

    hits.sort(key=lambda h: h.score, reverse=True)

    # Best-effort cache write; failures are non-fatal.
    try:
        _save_cache(kb_dir, cache)
    except OSError:
        pass

    return OverlapResult(
        max_overlap=max_overlap,
        top_hits=hits[: max(top_k, 0)],
        scanned_pages=len(pages),
    )


def overlap_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return overlap-related thresholds from auto_review config."""
    block: Mapping[str, Any] = {}
    if isinstance(config, Mapping):
        outer = config.get("auto_review")
        if isinstance(outer, Mapping):
            inner = outer.get("overlap")
            if isinstance(inner, Mapping):
                block = inner
    return {
        "wiki_dirs": tuple(block.get("wiki_dirs") or _DEFAULT_WIKI_DIRS),
        "top_k": int(block.get("top_k") or _DEFAULT_TOP_K),
        "min_report_score": float(block.get("min_report_score") or _DEFAULT_MIN_SCORE),
        "reject_threshold": float(block.get("reject_threshold") or 0.80),
        "hold_threshold": float(block.get("hold_threshold") or 0.60),
    }
