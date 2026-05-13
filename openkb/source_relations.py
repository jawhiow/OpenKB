"""Source document relationship helpers for OpenKB.

This module keeps CLI and browser-client document views on the same storage
rules. Source documents are tracked in ``.openkb/hashes.json``. Generated wiki
pages point back to source summaries through simple frontmatter:
``sources: [summaries/<doc>.md]``.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


GENERATED_PAGE_DIRS = ("companies", "industries", "concepts")
LEGACY_GENERATED_PAGE_DIRS = ("themes", "metrics", "risks")
ALL_MANAGED_PAGE_DIRS = (*GENERATED_PAGE_DIRS, *LEGACY_GENERATED_PAGE_DIRS)
RELATED_PAGE_GROUPS = ("summaries", *GENERATED_PAGE_DIRS)

_SOURCE_LIST_RE = re.compile(r"^sources:\s*\[(.*?)\]\s*$", re.MULTILINE)
_FULL_TEXT_RE = re.compile(r"^full_text:\s*(.+?)\s*$", re.MULTILINE)
_INGEST_LOG_RE = re.compile(r"^## \[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ingest \| (?P<name>.+?)\s*$", re.MULTILINE)


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def _format_ingested_at(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tzinfo())
    return dt.astimezone(_local_tzinfo()).isoformat(timespec="seconds")


def current_ingested_at() -> str:
    """Return an ISO timestamp for a source document ingested now."""
    return _format_ingested_at(datetime.now().astimezone())


def _parse_ingested_at(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_local_tzinfo())
    return parsed


def _normalize_ingested_at(value: object) -> str | None:
    parsed = _parse_ingested_at(value)
    return _format_ingested_at(parsed) if parsed is not None else None


def _ingested_date(value: object) -> str | None:
    parsed = _parse_ingested_at(value)
    return parsed.astimezone(_local_tzinfo()).date().isoformat() if parsed is not None else None


def _read_ingest_log_dates(kb_dir: Path) -> dict[str, str]:
    log_path = Path(kb_dir) / "wiki" / "log.md"
    if not log_path.exists():
        return {}
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    dates: dict[str, datetime] = {}
    for match in _INGEST_LOG_RE.finditer(text):
        name = _safe_doc_name(match.group("name"))
        try:
            parsed = datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        existing = dates.get(name)
        if existing is None or parsed > existing:
            dates[name] = parsed
    return {name: _format_ingested_at(value) for name, value in dates.items()}


def _mtime_ingested_at(kb_dir: Path, name: str) -> str | None:
    stem = Path(name).stem
    candidates = [
        Path(kb_dir) / "wiki" / "summaries" / f"{stem}.md",
        Path(kb_dir) / "wiki" / "sources" / f"{stem}.md",
        Path(kb_dir) / "wiki" / "sources" / f"{stem}.json",
        Path(kb_dir) / "raw" / name,
    ]
    candidates.extend(sorted((Path(kb_dir) / ".openkb" / "sources").glob(f"*/{stem}.md")))
    candidates.extend(sorted((Path(kb_dir) / ".openkb" / "sources").glob(f"*/{stem}.json")))
    candidates.extend(sorted((Path(kb_dir) / ".openkb" / "raw").glob(f"*/{Path(name).name}")))
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    newest = max(existing, key=lambda path: path.stat().st_mtime)
    return _format_ingested_at(datetime.fromtimestamp(newest.stat().st_mtime))


def _resolve_ingested_at(kb_dir: Path, meta: dict[str, Any], name: str, ingest_log_dates: dict[str, str] | None = None) -> str | None:
    existing = _normalize_ingested_at(meta.get("ingested_at"))
    if existing:
        return existing
    if ingest_log_dates is None:
        ingest_log_dates = _read_ingest_log_dates(kb_dir)
    from_log = ingest_log_dates.get(name)
    if from_log:
        return from_log
    return _mtime_ingested_at(kb_dir, name)



def _hashes_path(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / "hashes.json"


def normalize_kb_relative_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("/")


def kb_relative_path(kb_dir: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(kb_dir).as_posix()
    except ValueError:
        return str(path)


def resolve_kb_relative_path(kb_dir: Path, relative_path: object) -> Path | None:
    normalized = normalize_kb_relative_path(relative_path)
    if not normalized:
        return None
    return Path(kb_dir) / normalized


def staged_raw_dir(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / "raw"


def staged_raw_date_dir(kb_dir: Path, ingested_at: object = None) -> Path:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    return staged_raw_dir(kb_dir) / date_part


def staged_raw_relative_path(name: str, ingested_at: object = None) -> str:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    return f".openkb/raw/{date_part}/{Path(name).name}"


def staged_raw_full_path(kb_dir: Path, name: str, ingested_at: object = None) -> Path:
    return staged_raw_date_dir(kb_dir, ingested_at) / Path(name).name


def staged_sources_dir(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / "sources"


def staged_source_date_dir(kb_dir: Path, ingested_at: object = None) -> Path:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    return staged_sources_dir(kb_dir) / date_part


def staged_source_relative_path(stem: str, suffix: str, ingested_at: object = None) -> str:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return f".openkb/sources/{date_part}/{stem}{normalized_suffix}"


def staged_source_full_path(kb_dir: Path, stem: str, suffix: str, ingested_at: object = None) -> Path:
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return staged_source_date_dir(kb_dir, ingested_at) / f"{stem}{normalized_suffix}"


def staged_source_images_relative_dir(stem: str, ingested_at: object = None) -> str:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    return f".openkb/sources/images/{date_part}/{stem}"


def staged_source_images_full_dir(kb_dir: Path, stem: str, ingested_at: object = None) -> Path:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    return Path(kb_dir) / ".openkb" / "sources" / "images" / date_part / stem


def formal_raw_relative_path(name: str) -> str:
    return f"raw/{Path(name).name}"


def formal_raw_full_path(kb_dir: Path, name: str) -> Path:
    return Path(kb_dir) / formal_raw_relative_path(name)


def formal_source_relative_path(stem: str, suffix: str) -> str:
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return f"wiki/sources/{stem}{normalized_suffix}"


def formal_source_full_path(kb_dir: Path, stem: str, suffix: str) -> Path:
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return Path(kb_dir) / "wiki" / "sources" / f"{stem}{normalized_suffix}"


def formal_source_images_relative_dir(stem: str) -> str:
    return f"wiki/sources/images/{stem}"


def formal_source_images_full_dir(kb_dir: Path, stem: str) -> Path:
    return Path(kb_dir) / "wiki" / "sources" / "images" / stem


def resolve_source_artifact_path(kb_dir: Path, relative_path: object) -> Path | None:
    normalized = normalize_kb_relative_path(relative_path)
    if not normalized:
        return None
    candidates = [Path(kb_dir) / normalized]
    if normalized.startswith("sources/"):
        candidates.append(Path(kb_dir) / "wiki" / normalized)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def review_summary_storage_relative_path(relative_path: object) -> str:
    normalized = normalize_kb_relative_path(relative_path)
    if normalized.startswith(".openkb/"):
        normalized = normalized[len(".openkb/"):]
    return normalized


def review_summary_storage_full_path(kb_dir: Path, relative_path: object) -> Path | None:
    normalized = review_summary_storage_relative_path(relative_path)
    if not normalized:
        return None
    return Path(kb_dir) / ".openkb" / normalized


def source_images_dir_for_source_path(source_relative_path: object) -> str | None:
    normalized = normalize_kb_relative_path(source_relative_path)
    if not normalized:
        return None
    source_path = Path(normalized)
    stem = source_path.stem
    if normalized.startswith(".openkb/sources/"):
        parts = source_path.parts
        if len(parts) >= 4:
            date_part = parts[2]
            return f".openkb/sources/images/{date_part}/{stem}"
    if normalized.startswith("wiki/sources/"):
        return f"wiki/sources/images/{stem}"
    if normalized.startswith("sources/"):
        return f"wiki/sources/images/{stem}"
    return None


def _read_hashes(kb_dir: Path) -> dict[str, dict[str, Any]]:
    path = _hashes_path(kb_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(file_hash): meta
        for file_hash, meta in data.items()
        if isinstance(meta, dict)
    }


def _write_hashes(kb_dir: Path, hashes: dict[str, dict[str, Any]]) -> None:
    path = _hashes_path(kb_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_doc_name(name: object) -> str:
    value = str(name or "unknown").strip() or "unknown"
    return Path(value).name


def review_summaries_dir(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / "review_summaries"


def review_summary_date_dir(kb_dir: Path, ingested_at: object = None) -> Path:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    return review_summaries_dir(kb_dir) / date_part


def review_summary_relative_path(stem: str, ingested_at: object = None) -> str:
    date_part = _ingested_date(ingested_at) or datetime.now().astimezone(_local_tzinfo()).date().isoformat()
    return f"review_summaries/{date_part}/{stem}.md"


def review_summary_full_path(kb_dir: Path, stem: str, ingested_at: object = None) -> Path:
    return review_summary_date_dir(kb_dir, ingested_at) / f"{stem}.md"


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    close_end = end + len("\n---")
    frontmatter = text[:close_end]
    body = text[close_end:].lstrip("\r\n")
    return frontmatter, body


def _frontmatter_source_entries(text: str) -> list[str]:
    split = _split_frontmatter(text)
    if split is None:
        return []
    frontmatter, _body = split
    match = _SOURCE_LIST_RE.search(frontmatter)
    if not match:
        return []
    return [
        item.strip().strip("'\"")
        for item in match.group(1).split(",")
        if item.strip()
    ]


def _frontmatter_full_text(text: str) -> str | None:
    split = _split_frontmatter(text)
    if split is None:
        return None
    frontmatter, _body = split
    match = _FULL_TEXT_RE.search(frontmatter)
    if not match:
        return None
    return match.group(1).strip().strip("'\"")


def _update_frontmatter_sources(text: str, sources: list[str]) -> str:
    split = _split_frontmatter(text)
    if split is None:
        return text
    frontmatter, body = split
    replacement = f"sources: [{', '.join(sources)}]"
    if _SOURCE_LIST_RE.search(frontmatter):
        frontmatter = _SOURCE_LIST_RE.sub(replacement, frontmatter, count=1)
    else:
        frontmatter = frontmatter.replace("---\n", f"---\n{replacement}\n", 1)
    return frontmatter + "\n\n" + body


def _remove_summary_link(text: str, doc_stem: str) -> str:
    link = f"[[summaries/{doc_stem}]]"
    link_with_md = f"[[summaries/{doc_stem}.md]]"
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")) and (link in stripped or link_with_md in stripped):
            continue
        kept.append(line.replace(link_with_md, doc_stem).replace(link, doc_stem))
    return "\n".join(kept).rstrip() + "\n"


def _page_entry(path: Path, wiki_dir: Path, sources: list[str] | None = None) -> dict[str, Any]:
    rel = path.relative_to(wiki_dir).as_posix()
    return {
        "path": rel,
        "page": path.relative_to(wiki_dir).with_suffix("").as_posix(),
        "title": path.stem,
        "shared": bool(sources and len(sources) > 1),
    }


def _empty_related_pages() -> dict[str, list[dict[str, Any]]]:
    return {group: [] for group in RELATED_PAGE_GROUPS}


def _source_candidates(kb_dir: Path, stem: str) -> list[Path]:
    wiki_dir = kb_dir / "wiki"
    staged_matches = sorted((Path(kb_dir) / ".openkb" / "sources").glob(f"*/{stem}.md"))
    staged_matches.extend(sorted((Path(kb_dir) / ".openkb" / "sources").glob(f"*/{stem}.json")))
    return [
        wiki_dir / "sources" / f"{stem}.md",
        wiki_dir / "sources" / f"{stem}.json",
        *staged_matches,
    ]


def _metadata_raw_relative_path(kb_dir: Path, meta: dict[str, Any], name: str, ingested_at: object = None) -> str:
    stored = normalize_kb_relative_path(meta.get("raw_path"))
    if stored:
        return stored
    legacy = formal_raw_relative_path(name)
    if (Path(kb_dir) / legacy).exists():
        return legacy
    return staged_raw_relative_path(name, ingested_at)


def _display_source_relative_path(relative_path: str) -> str:
    if relative_path.startswith("wiki/"):
        return relative_path[len("wiki/"):]
    return relative_path


def _metadata_source_relative_path(kb_dir: Path, meta: dict[str, Any], stem: str) -> str | None:
    stored = normalize_kb_relative_path(meta.get("source_path"))
    if stored:
        return _display_source_relative_path(stored)
    candidate = next((path for path in _source_candidates(kb_dir, stem) if path.exists()), None)
    if candidate is not None:
        return _display_source_relative_path(kb_relative_path(kb_dir, candidate))
    return None


def _resolve_source_display_path(
    kb_dir: Path,
    meta: dict[str, Any],
    stem: str,
    summary_source_texts: dict[str, str | None] | None,
) -> str | None:
    if summary_source_texts is not None and stem in summary_source_texts:
        source_text_path = summary_source_texts.get(stem)
    else:
        source_text_path = _source_path_from_summary(kb_dir, stem)
    if source_text_path:
        return _display_source_relative_path(normalize_kb_relative_path(source_text_path))
    return _metadata_source_relative_path(kb_dir, meta, stem)


def _source_path_from_summary(kb_dir: Path, stem: str) -> str | None:
    summary_path = kb_dir / "wiki" / "summaries" / f"{stem}.md"
    if not summary_path.exists():
        return None
    try:
        return _frontmatter_full_text(summary_path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _build_related_page_index(kb_dir: Path) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, str | None]]:
    kb_dir = Path(kb_dir)
    wiki_dir = kb_dir / "wiki"
    related_pages_index: dict[str, dict[str, list[dict[str, Any]]]] = {
        group: {} for group in RELATED_PAGE_GROUPS
    }
    summary_source_texts: dict[str, str | None] = {}

    summary_dir = wiki_dir / "summaries"
    if summary_dir.exists():
        for path in sorted(summary_dir.glob("*.md")):
            stem = path.stem
            source_summary = f"summaries/{stem}.md"
            try:
                summary_source_texts[stem] = _frontmatter_full_text(path.read_text(encoding="utf-8"))
            except OSError:
                summary_source_texts[stem] = None
            related_pages_index["summaries"][source_summary] = [_page_entry(path, wiki_dir)]

    for subdir in GENERATED_PAGE_DIRS:
        pages_dir = wiki_dir / subdir
        if not pages_dir.exists():
            continue
        for path in sorted(pages_dir.glob("*.md")):
            try:
                sources = _frontmatter_source_entries(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if not sources:
                continue
            entry = _page_entry(path, wiki_dir, sources)
            for source_summary in sources:
                related_pages_index[subdir].setdefault(source_summary, []).append(entry)

    return related_pages_index, summary_source_texts


def _resolve_source_document_meta(kb_dir: Path, selector: str) -> tuple[str, dict[str, Any]]:
    hashes = _read_hashes(kb_dir)
    needle = str(selector or "").strip()
    if not needle:
        raise ValueError("Source document selector is required.")

    exact = hashes.get(needle)
    if exact is not None:
        return needle, exact

    needle_fold = needle.casefold()
    matches = [
        (file_hash, meta)
        for file_hash, meta in hashes.items()
        if file_hash.startswith(needle)
        or str(meta.get("name", "")).casefold() == needle_fold
        or str(Path(str(meta.get("name", "")).strip() or "").stem).casefold() == needle_fold
    ]
    if not matches:
        raise ValueError(f"No indexed source document matches: {selector}")
    if len(matches) > 1:
        names = ", ".join(sorted(_safe_doc_name(meta.get("name")) for _hash, meta in matches))
        raise ValueError(f"Ambiguous source document selector {selector!r}: {names}")
    return matches[0]


def _build_source_document(
    kb_dir: Path,
    file_hash: str,
    meta: dict[str, Any],
    *,
    related_pages_index: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    summary_source_texts: dict[str, str | None] | None = None,
    ingest_log_dates: dict[str, str] | None = None,
) -> dict[str, Any]:
    kb_dir = Path(kb_dir)
    wiki_dir = kb_dir / "wiki"
    name = _safe_doc_name(meta.get("name"))
    stem = Path(name).stem
    source_summary = f"summaries/{stem}.md"
    review_summary_path = review_summary_relative_path(stem, _resolve_ingested_at(kb_dir, meta, name, ingest_log_dates))
    formal_summary_exists = (wiki_dir / "summaries" / f"{stem}.md").exists()

    related_pages = _empty_related_pages()
    if related_pages_index is not None:
        for group in RELATED_PAGE_GROUPS:
            related_pages[group] = list(related_pages_index.get(group, {}).get(source_summary, []))
    else:
        summary_path = wiki_dir / "summaries" / f"{stem}.md"
        if summary_path.exists():
            related_pages["summaries"].append(_page_entry(summary_path, wiki_dir))
        for subdir in GENERATED_PAGE_DIRS:
            pages_dir = wiki_dir / subdir
            if not pages_dir.exists():
                continue
            for path in sorted(pages_dir.glob("*.md")):
                try:
                    sources = _frontmatter_source_entries(path.read_text(encoding="utf-8"))
                except OSError:
                    continue
                if source_summary in sources:
                    related_pages[subdir].append(_page_entry(path, wiki_dir, sources))

    ingested_at = _resolve_ingested_at(kb_dir, meta, name, ingest_log_dates)
    source_text_path = _resolve_source_display_path(kb_dir, meta, stem, summary_source_texts)
    raw_rel = _metadata_raw_relative_path(kb_dir, meta, name, ingested_at)
    raw_path = resolve_kb_relative_path(kb_dir, raw_rel)
    review_summary_path = review_summary_relative_path(stem, ingested_at)
    review_summary_exists = (Path(kb_dir) / ".openkb" / review_summary_path).exists()
    related_count = sum(len(items) for items in related_pages.values())
    return {
        "hash": file_hash,
        "name": name,
        "stem": stem,
        "type": meta.get("type", "unknown"),
        "pages": meta.get("pages", ""),
        "ingested_at": ingested_at,
        "ingested_date": _ingested_date(ingested_at),
        "raw_path": raw_rel,
        "raw_exists": bool(raw_path and raw_path.exists()),
        "source_path": source_text_path,
        "source_summary": source_summary,
        "summary_exists": formal_summary_exists,
        "review_summary_path": review_summary_path,
        "review_summary_exists": review_summary_exists,
        "related_count": related_count,
        "related_pages": related_pages,
    }


def get_source_documents(kb_dir: Path) -> list[dict[str, Any]]:
    """Return indexed source documents with their generated wiki pages."""
    kb_dir = Path(kb_dir)
    hashes = _read_hashes(kb_dir)
    related_pages_index, summary_source_texts = _build_related_page_index(kb_dir)
    ingest_log_dates = _read_ingest_log_dates(kb_dir)
    return [
        _build_source_document(
            kb_dir,
            file_hash,
            meta,
            related_pages_index=related_pages_index,
            summary_source_texts=summary_source_texts,
            ingest_log_dates=ingest_log_dates,
        )
        for file_hash, meta in hashes.items()
    ]


def resolve_source_document(kb_dir: Path, selector: str) -> dict[str, Any]:
    """Resolve a source document by hash, hash prefix, file name, or stem."""
    kb_dir = Path(kb_dir)
    file_hash, meta = _resolve_source_document_meta(kb_dir, selector)
    related_pages_index, summary_source_texts = _build_related_page_index(kb_dir)
    ingest_log_dates = _read_ingest_log_dates(kb_dir)
    return _build_source_document(
        kb_dir,
        file_hash,
        meta,
        related_pages_index=related_pages_index,
        summary_source_texts=summary_source_texts,
        ingest_log_dates=ingest_log_dates,
    )


def backfill_source_ingest_dates(kb_dir: Path) -> dict[str, Any]:
    """Persist missing source ingestion timestamps into `.openkb/hashes.json`."""
    kb_dir = Path(kb_dir)
    hashes = _read_hashes(kb_dir)
    ingest_log_dates = _read_ingest_log_dates(kb_dir)
    updated = 0
    skipped = 0
    missing = 0
    for _file_hash, meta in hashes.items():
        name = _safe_doc_name(meta.get("name"))
        existing = _normalize_ingested_at(meta.get("ingested_at"))
        if existing:
            if existing != meta.get("ingested_at"):
                meta["ingested_at"] = existing
                updated += 1
            else:
                skipped += 1
            continue
        ingested_at = _resolve_ingested_at(kb_dir, meta, name, ingest_log_dates)
        if not ingested_at:
            missing += 1
            continue
        meta["ingested_at"] = ingested_at
        updated += 1
    if updated:
        _write_hashes(kb_dir, hashes)
    return {"updated": updated, "skipped": skipped, "missing": missing, "total": len(hashes)}


def get_source_document_detail(kb_dir: Path, selector: str) -> dict[str, Any]:
    """Return one indexed source document and its generated wiki pages."""
    return resolve_source_document(kb_dir, selector)


def _remove_index_entries(wiki_dir: Path, page_ids: set[str]) -> None:
    if not page_ids:
        return
    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        return
    lines = index_path.read_text(encoding="utf-8").splitlines()
    filtered = [
        line for line in lines
        if not any(line.startswith(f"- [[{page_id}]]") for page_id in page_ids)
    ]
    if filtered != lines:
        index_path.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")


def _remove_evidence_for_source(
    wiki_dir: Path,
    source_summary: str,
    removed_pages: set[str],
    stem: str,
    *,
    source_path: str = "",
) -> None:
    map_path = wiki_dir / "evidence_map.json"
    if not map_path.exists():
        return
    try:
        evidence_map = json.loads(map_path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return
    if not isinstance(evidence_map, dict):
        return

    summary_link = source_summary[:-3] if source_summary.endswith(".md") else source_summary
    source_texts = {f"sources/{stem}.md", f"sources/{stem}.json"}
    normalized_source_path = normalize_kb_relative_path(source_path)
    if normalized_source_path:
        source_texts.add(normalized_source_path.removeprefix("wiki/"))
        source_texts.add(normalized_source_path)
    for page in list(evidence_map):
        if page in removed_pages:
            evidence_map.pop(page, None)
            continue
        entries = evidence_map.get(page)
        if not isinstance(entries, list):
            evidence_map.pop(page, None)
            continue
        kept = [
            entry for entry in entries
            if isinstance(entry, dict)
            and entry.get("source") not in {source_summary, *source_texts}
            and entry.get("summary") != summary_link
        ]
        if kept:
            evidence_map[page] = kept
        else:
            evidence_map.pop(page, None)

    map_path.write_text(json.dumps(evidence_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _remove_path(path: Path, kb_dir: Path) -> str | None:
    try:
        resolved = path.resolve()
        root = kb_dir.resolve()
        if not resolved.is_relative_to(root) or not path.exists():
            return None
        if path.is_dir():
            shutil.rmtree(path)
            return path.relative_to(kb_dir).as_posix() + "/"
        path.unlink()
        return path.relative_to(kb_dir).as_posix()
    except OSError:
        return None


def delete_source_document(kb_dir: Path, selector: str) -> dict[str, Any]:
    """Delete an indexed source document and safely clean related wiki pages.

    Pages whose only source is this document are deleted. Shared generated pages
    are preserved; their frontmatter, related-summary link, and evidence entries
    are updated to remove the deleted source.
    """
    kb_dir = Path(kb_dir)
    wiki_dir = kb_dir / "wiki"
    document = resolve_source_document(kb_dir, selector)
    stem = document["stem"]
    source_summary = document["source_summary"]
    review_summary_path = review_summary_storage_relative_path(document.get("review_summary_path"))
    source_path = normalize_kb_relative_path(document.get("source_path"))
    raw_path = normalize_kb_relative_path(document.get("raw_path"))
    removed_pages: list[str] = []
    updated_pages: list[str] = []
    removed_files: list[str] = []

    summary_path = wiki_dir / source_summary
    if summary_path.exists():
        summary_path.unlink()
        removed_pages.append(source_summary)

    if review_summary_path:
        review_summary_full_path = review_summary_storage_full_path(kb_dir, review_summary_path)
        removed_review_summary = _remove_path(review_summary_full_path, kb_dir) if review_summary_full_path else None
        if removed_review_summary:
            removed_files.append(removed_review_summary)

    for subdir in ALL_MANAGED_PAGE_DIRS:
        pages_dir = wiki_dir / subdir
        if not pages_dir.exists():
            continue
        for path in sorted(pages_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            sources = _frontmatter_source_entries(text)
            if source_summary not in sources:
                continue
            rel = path.relative_to(wiki_dir).as_posix()
            remaining = [source for source in sources if source != source_summary]
            if not remaining:
                path.unlink()
                removed_pages.append(rel)
                continue
            updated = _update_frontmatter_sources(text, remaining)
            updated = _remove_summary_link(updated, stem)
            path.write_text(updated, encoding="utf-8")
            updated_pages.append(rel)

    raw_removed = _remove_path(resolve_kb_relative_path(kb_dir, raw_path), kb_dir) if raw_path else None
    if raw_removed:
        removed_files.append(raw_removed)
    source_candidates: list[Path] = []
    if source_path:
        source_artifact = resolve_source_artifact_path(kb_dir, source_path)
        if source_artifact is not None:
            source_candidates.append(source_artifact)
    for candidate in _source_candidates(kb_dir, stem):
        if candidate not in source_candidates:
            source_candidates.append(candidate)
    for candidate in source_candidates:
        removed = _remove_path(candidate, kb_dir)
        if removed and removed not in removed_files:
            removed_files.append(removed)
    images_dir = source_images_dir_for_source_path(source_path) if source_path else None
    images_removed = _remove_path(resolve_kb_relative_path(kb_dir, images_dir), kb_dir) if images_dir else None
    if images_removed:
        removed_files.append(images_removed)

    removed_page_ids = {Path(page).with_suffix("").as_posix() for page in removed_pages}
    _remove_index_entries(wiki_dir, removed_page_ids)
    _remove_evidence_for_source(
        wiki_dir,
        source_summary,
        set(removed_pages),
        stem,
        source_path=source_path,
    )

    hashes = _read_hashes(kb_dir)
    hashes.pop(document["hash"], None)
    _write_hashes(kb_dir, hashes)

    return {
        "document": document,
        "removed_pages": removed_pages,
        "updated_pages": updated_pages,
        "removed_files": removed_files,
    }
