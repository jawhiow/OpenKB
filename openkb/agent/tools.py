"""Plain wiki tool functions for the OpenKB agent.

These functions are intentionally NOT decorated with ``@function_tool`` here.
Decoration happens when building the agent so that the same functions can be
tested in isolation without requiring the openai-agents runtime.
"""
from __future__ import annotations

import json as _json
import re
from collections import Counter
from pathlib import Path


def list_wiki_files(directory: str, wiki_root: str) -> str:
    """List all Markdown files in a wiki subdirectory.

    Args:
        directory: Subdirectory path relative to *wiki_root* (e.g. ``"sources"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        Newline-separated list of ``.md`` filenames found in *directory*,
        or ``"No files found."`` if the directory is empty or does not exist.
    """
    root = Path(wiki_root).resolve()
    target = (root / directory).resolve()
    if not target.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not target.exists() or not target.is_dir():
        return "No files found."

    md_files = sorted(p.name for p in target.iterdir() if p.suffix == ".md")
    if not md_files:
        return "No files found."
    return "\n".join(md_files)


def read_wiki_file(path: str, wiki_root: str) -> str:
    """Read a Markdown file from the wiki.

    Args:
        path: File path relative to *wiki_root* (e.g. ``"sources/notes.md"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        File contents as a string, or ``"File not found: {path}"`` if missing.
    """
    root = Path(wiki_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not full_path.exists():
        return f"File not found: {path}"
    return full_path.read_text(encoding="utf-8")


def parse_pages(pages: str) -> list[int]:
    """Parse a page specification string into a sorted, deduplicated list of page numbers.

    Args:
        pages: Page spec such as ``"3-5,7,10-12"``.

    Returns:
        Sorted list of positive page numbers, e.g. ``[3, 4, 5, 7, 10, 11, 12]``.
    """
    result: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            # Handle ranges like "3-5"; also handle negative numbers by only
            # splitting on the first "-" that follows a digit.
            segments = part.split("-")
            # Re-join to handle leading negatives: segments[0] may be empty
            # if part starts with "-".  We just try to parse start/end.
            try:
                if len(segments) == 2:
                    start, end = int(segments[0]), int(segments[1])
                    result.update(range(start, end + 1))
                elif len(segments) == 3 and segments[0] == "":
                    # e.g. "-1" split gives ['', '1']
                    result.add(-int(segments[1]))
                # More complex cases (e.g. negative range) are ignored.
            except ValueError:
                pass
        else:
            try:
                result.add(int(part))
            except ValueError:
                pass
    return sorted(n for n in result if n > 0)


def get_wiki_page_content(doc_name: str, pages: str, wiki_root: str) -> str:
    """Return formatted content for specified pages of a document.

    Reads ``{wiki_root}/sources/{doc_name}.json`` which must be a JSON array of
    objects with at least ``{"page": int, "content": str}`` fields and an
    optional ``"images"`` list of ``{"path": str, ...}`` objects.

    Args:
        doc_name: Document name without extension (e.g. ``"paper"``).
        pages: Page specification string (e.g. ``"1-3,7"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        Formatted page content, or an error message string.
    """
    root = Path(wiki_root).resolve()
    target = (root / "sources" / f"{doc_name}.json").resolve()
    if not target.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not target.exists():
        return f"File not found: sources/{doc_name}.json"

    data = _json.loads(target.read_text(encoding="utf-8"))
    requested = set(parse_pages(pages))
    matches = [entry for entry in data if entry.get("page") in requested]

    if not matches:
        return f"No content found for pages {pages} in {doc_name}."

    parts: list[str] = []
    for entry in matches:
        page_num = entry["page"]
        content = entry.get("content", "")
        block = f"[Page {page_num}]\n{content}"
        images = entry.get("images")
        if images:
            paths = ", ".join(img["path"] for img in images if "path" in img)
            if paths:
                block += f"\n[Images: {paths}]"
        parts.append(block)

    return "\n\n".join(parts) + "\n\n"


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.%+-]*|[\u4e00-\u9fff]+", re.IGNORECASE)
_PAGE_RANGE_RE = re.compile(
    r"^\s*#{1,6}\s+(?P<title>.*?)\s+\(pages?\s+"
    r"(?P<start>\d+)\s*[-\u2013\u2014]\s*(?P<end>\d+)\)",
    re.IGNORECASE,
)
_LONG_DOC_TYPES = {"pageindex", "local-long"}


def _search_terms(text: str) -> list[str]:
    terms: list[str] = []
    for raw in _WORD_RE.findall(text.lower()):
        if not raw:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw):
            if len(raw) == 1:
                terms.append(raw)
            else:
                terms.append(raw)
                terms.extend(raw[index : index + 2] for index in range(len(raw) - 1))
            continue
        if len(raw) >= 2:
            terms.append(raw)
    return terms


def _score_text(text: str, query_terms: list[str], query_phrase: str) -> int:
    if not query_terms:
        return 0
    counts = Counter(_search_terms(text))
    score = sum(counts.get(term, 0) for term in query_terms)
    normalized_text = " ".join(text.lower().split())
    if query_phrase and query_phrase in normalized_text:
        score += max(len(query_terms) * 4, 4)
    return score


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    metadata: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return metadata, "\n".join(lines[index + 1 :])
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("'\"")
    return metadata, text


def _summary_doc_name(metadata: dict[str, str], summary_path: Path) -> str:
    full_text = metadata.get("full_text", "")
    if full_text:
        return Path(full_text).stem
    return summary_path.stem


def _parse_summary_nodes(markdown_body: str) -> list[dict]:
    nodes: list[dict] = []
    current: dict | None = None

    for line in markdown_body.splitlines():
        match = _PAGE_RANGE_RE.match(line)
        if match:
            if current is not None:
                nodes.append(current)
            start = int(match.group("start"))
            end = int(match.group("end"))
            if end < start:
                start, end = end, start
            current = {
                "title": match.group("title").strip(),
                "start": start,
                "end": end,
                "text": [],
            }
            continue
        if current is not None and line.strip():
            current["text"].append(line.strip())

    if current is not None:
        nodes.append(current)
    return nodes


def _resolve_source_path(root: Path, metadata: dict[str, str], doc_name: str) -> Path:
    full_text = metadata.get("full_text", "")
    if full_text:
        return (root / full_text).resolve()
    return (root / "sources" / f"{doc_name}.json").resolve()


def _iter_long_document_summaries(root: Path, doc_name: str) -> list[dict]:
    summaries_dir = root / "summaries"
    if not summaries_dir.exists():
        return []

    requested_raw = doc_name.strip().lower()
    requested_names = {requested_raw, Path(requested_raw).stem} if requested_raw else set()
    documents: list[dict] = []
    for summary_path in sorted(summaries_dir.glob("*.md")):
        text = summary_path.read_text(encoding="utf-8")
        metadata, body = _split_frontmatter(text)
        doc_type = metadata.get("doc_type", "").strip().lower()
        if doc_type not in _LONG_DOC_TYPES:
            continue
        resolved_doc_name = _summary_doc_name(metadata, summary_path)
        document_names = {resolved_doc_name.lower(), summary_path.stem.lower()}
        if requested_names and document_names.isdisjoint(requested_names):
            continue
        source_path = _resolve_source_path(root, metadata, resolved_doc_name)
        if not source_path.is_relative_to(root) or source_path.suffix.lower() != ".json":
            continue
        if not source_path.exists():
            continue
        documents.append(
            {
                "doc_name": resolved_doc_name,
                "summary_path": summary_path,
                "source_path": source_path,
                "nodes": _parse_summary_nodes(body),
            }
        )
    return documents


def _load_source_pages(source_path: Path) -> list[dict]:
    data = _json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []

    pages: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            page_num = int(entry.get("page"))
        except (TypeError, ValueError):
            continue
        if page_num <= 0:
            continue
        pages.append(
            {
                "page": page_num,
                "content": str(entry.get("content", "")),
                "images": entry.get("images") if isinstance(entry.get("images"), list) else [],
            }
        )
    return pages


def _page_node_score(page_num: int, nodes: list[dict], query_terms: list[str], query_phrase: str) -> tuple[int, str]:
    best_score = 0
    best_label = ""
    for node in nodes:
        if not (node["start"] <= page_num <= node["end"]):
            continue
        node_text = f"{node['title']}\n{' '.join(node['text'])}"
        score = _score_text(node_text, query_terms, query_phrase)
        if score > best_score:
            best_score = score
            best_label = f"{node['title']} pages {node['start']}-{node['end']}"
    return best_score, best_label


def _snippet(content: str, query_terms: list[str], max_chars: int) -> str:
    compact = " ".join(content.split())
    if len(compact) <= max_chars:
        return compact

    lower = compact.lower()
    first_hit = min(
        (index for term in query_terms for index in [lower.find(term)] if index >= 0),
        default=0,
    )
    start = max(first_hit - max_chars // 3, 0)
    end = min(start + max_chars, len(compact))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def search_long_document_pages(
    query: str,
    wiki_root: str,
    doc_name: str = "",
    top_k: int = 5,
    max_chars: int = 360,
) -> str:
    """Search locally indexed long documents and return likely relevant pages.

    The search is self-contained: it uses PageIndex-rendered summary trees under
    ``summaries/`` and per-page source JSON under ``sources/``. It does not need
    PageIndex credentials or a live PageIndex runtime at query time.
    """
    root = Path(wiki_root).resolve()
    if not root.exists():
        return f"Wiki root not found: {wiki_root}"

    query = query.strip()
    if not query:
        return "Query is required for long-document search."

    try:
        limit = min(max(int(top_k), 1), 20)
    except (TypeError, ValueError):
        limit = 5
    try:
        snippet_chars = min(max(int(max_chars), 120), 1200)
    except (TypeError, ValueError):
        snippet_chars = 360

    documents = _iter_long_document_summaries(root, doc_name)
    if not documents:
        suffix = f" matching {doc_name!r}" if doc_name else ""
        return f"No long-document sources found{suffix}."

    query_terms = _search_terms(query)
    query_phrase = " ".join(query.lower().split())
    candidates: list[dict] = []

    for document in documents:
        pages = _load_source_pages(document["source_path"])
        for page in pages:
            page_score = _score_text(page["content"], query_terms, query_phrase)
            node_score, node_label = _page_node_score(
                page["page"],
                document["nodes"],
                query_terms,
                query_phrase,
            )
            score = page_score * 3 + node_score
            if score <= 0:
                continue
            candidates.append(
                {
                    "score": score,
                    "page": page["page"],
                    "content": page["content"],
                    "doc_name": document["doc_name"],
                    "summary_path": document["summary_path"],
                    "source_path": document["source_path"],
                    "node_label": node_label,
                }
            )

    if not candidates:
        return f"No matching long-document pages found for query: {query}"

    candidates.sort(key=lambda item: (-item["score"], item["doc_name"], item["page"]))
    selected = candidates[:limit]

    lines = [f"Long document search results for: {query}"]
    for item in selected:
        page_spec = str(item["page"])
        summary_rel = item["summary_path"].relative_to(root).as_posix()
        source_rel = item["source_path"].relative_to(root).as_posix()
        lines.append("")
        lines.append(f"- {item['doc_name']} pages {page_spec} (score {item['score']})")
        if item["node_label"]:
            lines.append(f"  Matched tree node: {item['node_label']}")
        lines.append(f"  Summary: {summary_rel}")
        lines.append(f"  Source: {source_rel}")
        lines.append(f"  Snippet: {_snippet(item['content'], query_terms, snippet_chars)}")
        lines.append(
            f"  Next: get_page_content(doc_name=\"{item['doc_name']}\", pages=\"{page_spec}\")"
        )
    return "\n".join(lines)


_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def read_wiki_image(path: str, wiki_root: str) -> dict:
    """Read an image file from the wiki and return as base64 data URL.

    Args:
        path: Image path relative to *wiki_root* (e.g. ``"sources/images/doc/p1_img1.png"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        A dict with ``type``, ``image_url`` keys for ``ToolOutputImage``,
        or a dict with ``type``, ``text`` keys on error.
    """
    import base64

    root = Path(wiki_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return {"type": "text", "text": "Access denied: path escapes wiki root."}
    if not full_path.exists():
        return {"type": "text", "text": f"Image not found: {path}"}

    mime = _MIME_TYPES.get(full_path.suffix.lower(), "image/png")
    b64 = base64.b64encode(full_path.read_bytes()).decode()
    return {"type": "image", "image_url": f"data:{mime};base64,{b64}"}


def write_wiki_file(path: str, content: str, wiki_root: str) -> str:
    """Write or overwrite a Markdown file in the wiki.

    Parent directories are created automatically if they do not exist.

    Args:
        path: File path relative to *wiki_root* (e.g. ``"concepts/attention.md"``).
        content: Markdown content to write.
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        ``"Written: {path}"`` on success.
    """
    root = Path(wiki_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return f"Written: {path}"


def get_market_snapshot(entity_or_symbol: str, kb_root: str) -> dict:
    """Return the latest market snapshot for a company or xueqiu symbol.

    Resolution order:
        1. Exact xueqiu_symbol match in ``companies.yaml``.
        2. Registry alias / canonical_id lookup → identifiers.xueqiu_symbol.

    The result always includes ``source`` and ``as_of`` for citation, and a
    ``stale`` flag with ``stale_reason`` when applicable. Agents MUST surface
    those fields when citing any number from the snapshot.
    """
    kb_dir = Path(kb_root).resolve()
    try:
        from openkb.market_data.refresh import resolve_symbol
        from openkb.market_data.snapshot_store import read_quote_snapshot
    except Exception as exc:  # pragma: no cover
        return {"error": f"market_data subsystem unavailable: {exc}"}

    symbol, market, canonical_id = resolve_symbol(kb_dir, entity_or_symbol)
    if symbol is None or market is None:
        return {
            "error": "unresolved_entity_or_symbol",
            "input": entity_or_symbol,
            "canonical_id": canonical_id,
        }

    readout = read_quote_snapshot(kb_dir, symbol)
    if readout is None:
        return {
            "error": "no_snapshot_cached",
            "symbol": symbol,
            "market": market,
            "canonical_id": canonical_id,
            "hint": "Run `openkb market refresh --symbol " + symbol + "` first.",
        }

    data = readout.as_dict()
    data["canonical_id"] = canonical_id
    if readout.stale:
        data["disclaimer"] = (
            "Market data is stale (cache TTL expired). Re-run `openkb market refresh` "
            "to fetch a fresh snapshot."
        )
    return data

