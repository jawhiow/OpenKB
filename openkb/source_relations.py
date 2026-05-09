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
from pathlib import Path
from typing import Any


GENERATED_PAGE_DIRS = ("companies", "industries", "concepts")
LEGACY_GENERATED_PAGE_DIRS = ("themes", "metrics", "risks")
ALL_MANAGED_PAGE_DIRS = (*GENERATED_PAGE_DIRS, *LEGACY_GENERATED_PAGE_DIRS)
RELATED_PAGE_GROUPS = ("summaries", *GENERATED_PAGE_DIRS)

_SOURCE_LIST_RE = re.compile(r"^sources:\s*\[(.*?)\]\s*$", re.MULTILINE)
_FULL_TEXT_RE = re.compile(r"^full_text:\s*(.+?)\s*$", re.MULTILINE)


def _hashes_path(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / "hashes.json"


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
    return [
        wiki_dir / "sources" / f"{stem}.md",
        wiki_dir / "sources" / f"{stem}.json",
    ]


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
) -> dict[str, Any]:
    kb_dir = Path(kb_dir)
    wiki_dir = kb_dir / "wiki"
    name = _safe_doc_name(meta.get("name"))
    stem = Path(name).stem
    source_summary = f"summaries/{stem}.md"

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

    if summary_source_texts is not None and stem in summary_source_texts:
        source_text_path = summary_source_texts.get(stem)
    else:
        summary_path = wiki_dir / "summaries" / f"{stem}.md"
        if summary_path.exists():
            source_text_path = _frontmatter_full_text(summary_path.read_text(encoding="utf-8"))
        else:
            existing_source = next((path for path in _source_candidates(kb_dir, stem) if path.exists()), None)
            source_text_path = existing_source.relative_to(wiki_dir).as_posix() if existing_source else None

    related_count = sum(len(items) for items in related_pages.values())
    raw_path = kb_dir / "raw" / name
    return {
        "hash": file_hash,
        "name": name,
        "stem": stem,
        "type": meta.get("type", "unknown"),
        "pages": meta.get("pages", ""),
        "raw_path": f"raw/{name}",
        "raw_exists": raw_path.exists(),
        "source_path": source_text_path,
        "source_summary": source_summary,
        "summary_exists": (wiki_dir / "summaries" / f"{stem}.md").exists(),
        "related_count": related_count,
        "related_pages": related_pages,
    }


def get_source_documents(kb_dir: Path) -> list[dict[str, Any]]:
    """Return indexed source documents with their generated wiki pages."""
    kb_dir = Path(kb_dir)
    hashes = _read_hashes(kb_dir)
    related_pages_index, summary_source_texts = _build_related_page_index(kb_dir)
    return [
        _build_source_document(
            kb_dir,
            file_hash,
            meta,
            related_pages_index=related_pages_index,
            summary_source_texts=summary_source_texts,
        )
        for file_hash, meta in hashes.items()
    ]


def resolve_source_document(kb_dir: Path, selector: str) -> dict[str, Any]:
    """Resolve a source document by hash, hash prefix, file name, or stem."""
    kb_dir = Path(kb_dir)
    file_hash, meta = _resolve_source_document_meta(kb_dir, selector)
    related_pages_index, summary_source_texts = _build_related_page_index(kb_dir)
    return _build_source_document(
        kb_dir,
        file_hash,
        meta,
        related_pages_index=related_pages_index,
        summary_source_texts=summary_source_texts,
    )


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


def _remove_evidence_for_source(wiki_dir: Path, source_summary: str, removed_pages: set[str], stem: str) -> None:
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
    removed_pages: list[str] = []
    updated_pages: list[str] = []

    summary_path = wiki_dir / source_summary
    if summary_path.exists():
        summary_path.unlink()
        removed_pages.append(source_summary)

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

    removed_files: list[str] = []
    raw_removed = _remove_path(kb_dir / "raw" / document["name"], kb_dir)
    if raw_removed:
        removed_files.append(raw_removed)
    for source_path in _source_candidates(kb_dir, stem):
        removed = _remove_path(source_path, kb_dir)
        if removed:
            removed_files.append(removed)
    images_removed = _remove_path(wiki_dir / "sources" / "images" / stem, kb_dir)
    if images_removed:
        removed_files.append(images_removed)

    removed_page_ids = {Path(page).with_suffix("").as_posix() for page in removed_pages}
    _remove_index_entries(wiki_dir, removed_page_ids)
    _remove_evidence_for_source(wiki_dir, source_summary, set(removed_pages), stem)

    hashes = _read_hashes(kb_dir)
    hashes.pop(document["hash"], None)
    _write_hashes(kb_dir, hashes)

    return {
        "document": document,
        "removed_pages": removed_pages,
        "updated_pages": updated_pages,
        "removed_files": removed_files,
    }
