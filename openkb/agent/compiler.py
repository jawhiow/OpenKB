"""Wiki compilation pipeline for OpenKB.

Pipeline leveraging LLM prompt caching:
  Step 1: Build base context A (schema + document content).
  Step 2: A → generate summary.
  Step 3: A + summary → concepts plan (create/update/related).
  Step 4: Concurrent LLM calls (A cached) → generate new + rewrite updated concepts.
  Step 5: Code adds cross-ref links to related concepts, updates index.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

import litellm

from openkb.schema import get_agents_md
from openkb.llm_runtime import acompletion, completion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are OpenKB's wiki compilation agent for a personal knowledge base.

{schema_md}

Write all content in {language} language.
Use [[wikilinks]] to connect related pages (e.g. [[concepts/attention]]).
"""

_SUMMARY_USER = """\
New document: {doc_name}

Full text:
{content}

Write a summary page for this document in Markdown.

If this is an investment research report, use an investment-research structure:
- Core thesis and conclusion
- Ratings / top ideas / company table when available
- Key numbers, assumptions, forecasts, and valuation context
- Industry chain map and bottlenecks
- Catalysts and monitoring indicators
- Risks, bear-case evidence, and disconfirming signals
- Source evidence with page references when page markers are present

Keep all material claims traceable to the source text. Preserve important
numbers, dates, companies, and units. Use [[concepts/...]] only for concepts
that deserve durable cross-document pages.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown. Include key concepts, findings, ideas, \
and [[wikilinks]] to concepts that could become cross-document concept pages

Return ONLY valid JSON, no fences.
"""


_CONCEPTS_PLAN_USER = """\
Based on the summary above, decide how to update the wiki's concept pages.

Existing concept pages:
{concept_briefs}

Return a JSON object with three keys:

1. "create" — new concepts not covered by any existing page. Array of objects:
   {{"name": "concept-slug", "title": "Human-Readable Title"}}

2. "update" — existing concepts that have significant new information from \
this document worth integrating. Array of objects:
   {{"name": "existing-slug", "title": "Existing Title"}}

3. "related" — existing concepts tangentially related to this document but \
not needing content changes, just a cross-reference link. Array of slug strings.

Rules:
- Every [[concepts/...]] link used in the summary must appear in exactly one of
  create, update, or related.
- For investment research reports, create enough durable concepts to avoid
  broken links and preserve reusable investment knowledge. Prefer 5-8
  high-signal concepts over a shallow 2-3 concept cap when the report supports it.
- Do NOT create a concept that overlaps with an existing one — use "update".
- Do NOT create concepts that are just the document topic itself.
- "related" is for lightweight cross-linking only, no content rewrite needed.

Return ONLY valid JSON, no fences, no explanation.
"""

_CONCEPT_PAGE_USER = """\
Write the concept page for: {title}

This concept relates to the document "{doc_name}" summarized above.
{update_instruction}

If the source is investment research, structure the page as durable investment
knowledge: definition, why it matters, source evidence, key metrics to track,
company exposure, risks/contra-evidence, and related concepts. Include page
references when available in the summary or source context.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept
- "content": The full concept page in Markdown. Include clear explanation, \
key details from the source document, and [[wikilinks]] to related concepts \
and [[summaries/{doc_name}]]

Return ONLY valid JSON, no fences.
"""

_CONCEPT_UPDATE_USER = """\
Update the concept page for: {title}

Current content of this page:
{existing_content}

New information from document "{doc_name}" (summarized above) should be \
integrated into this page. Rewrite the full page incorporating the new \
information naturally — do not just append. Maintain existing \
[[wikilinks]] and add new ones where appropriate.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept (may differ from before)
- "content": The rewritten full concept page in Markdown

Return ONLY valid JSON, no fences.
"""

_LONG_DOC_SUMMARY_USER = """\
This is a PageIndex summary for long document "{doc_name}" (doc_id: {doc_id}):

{content}

Based on this structured summary, write a concise overview that captures \
the key themes and findings. This will be used to generate concept pages.

Return ONLY the Markdown content (no frontmatter, no code fences).
"""

_LOCAL_LONG_DOC_SUMMARY_USER = """\
This is a page-indexed local extraction for long document "{doc_name}".

{content}

Based on this page-indexed extraction, write a high-signal summary page.
For investment research reports, preserve ratings, company names, forecasts,
valuation context, key numbers, catalysts, risks, and monitoring indicators.
Use page references like "p.12" where evidence is available.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown with durable [[concepts/...]] links

Return ONLY valid JSON, no fences.
"""


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

class _Spinner:
    """Animated dots spinner that runs in a background thread."""

    def __init__(self, label: str):
        self._label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        sys.stdout.write(f"    {self._label}")
        sys.stdout.flush()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(timeout=1.0):
            sys.stdout.write(".")
            sys.stdout.flush()

    def stop(self, suffix: str = "") -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write(f" {suffix}\n")
        sys.stdout.flush()


def _format_usage(elapsed: float, usage) -> str:
    """Format timing and token usage into a short summary string."""
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    if input_tokens is None and hasattr(usage, "input_tokens"):
        input_tokens = usage.input_tokens
    if output_tokens is None and hasattr(usage, "output_tokens"):
        output_tokens = usage.output_tokens
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    cached = getattr(usage, "prompt_tokens_details", None)
    if cached is None:
        cached = getattr(usage, "input_tokens_details", None)
    cache_info = ""
    if cached and hasattr(cached, "cached_tokens") and cached.cached_tokens:
        cache_info = f", cached={cached.cached_tokens}"
    return f"{elapsed:.1f}s (in={input_tokens}, out={output_tokens}{cache_info})"


def _fmt_messages(messages: list[dict], max_content: int = 200) -> str:
    """Format messages for debug output, truncating long content."""
    parts = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if len(content) > max_content:
            preview = content[:max_content] + f"... ({len(content)} chars)"
        else:
            preview = content
        parts.append(f"      [{role}] {preview}")
    return "\n".join(parts)


def _llm_call(model: str, messages: list[dict], step_name: str, **kwargs) -> str:
    """Single LLM call with animated progress and debug logging."""
    logger.debug("LLM request [%s]:\n%s", step_name, _fmt_messages(messages))
    if kwargs:
        logger.debug("LLM kwargs [%s]: %s", step_name, kwargs)

    spinner = _Spinner(step_name)
    spinner.start()
    t0 = time.time()

    response = completion(model=model, messages=messages, **kwargs)
    content = response.text

    spinner.stop(_format_usage(time.time() - t0, response.usage))
    logger.debug("LLM response [%s]:\n%s", step_name, content[:500] + ("..." if len(content) > 500 else ""))
    return content.strip()


async def _llm_call_async(model: str, messages: list[dict], step_name: str) -> str:
    """Async LLM call with timing output and debug logging."""
    logger.debug("LLM request [%s]:\n%s", step_name, _fmt_messages(messages))

    t0 = time.time()

    response = await acompletion(model=model, messages=messages)
    content = response.text

    elapsed = time.time() - t0
    sys.stdout.write(f"    {step_name}... {_format_usage(elapsed, response.usage)}\n")
    sys.stdout.flush()
    logger.debug("LLM response [%s]:\n%s", step_name, content[:500] + ("..." if len(content) > 500 else ""))
    return content.strip()


def _parse_json(text: str) -> list | dict:
    """Parse JSON from LLM response, handling fences, prose, and malformed JSON."""
    from json_repair import repair_json
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        cleaned = cleaned[first_nl + 1:] if first_nl != -1 else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    result = json.loads(repair_json(cleaned.strip()))
    if not isinstance(result, (dict, list)):
        raise ValueError(f"Expected JSON object or array, got {type(result).__name__}")
    return result


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _read_wiki_context(wiki_dir: Path) -> tuple[str, list[str]]:
    """Read current index.md content and list of existing concept slugs."""
    index_path = wiki_dir / "index.md"
    index_content = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    concepts_dir = wiki_dir / "concepts"
    existing = sorted(p.stem for p in concepts_dir.glob("*.md")) if concepts_dir.exists() else []

    return index_content, existing


def _read_concept_briefs(wiki_dir: Path) -> str:
    """Read existing concept pages and return compact one-line summaries.

    For each concept, reads the ``brief:`` field from YAML frontmatter if
    present; otherwise falls back to truncating the first 150 chars of the body
    (newlines collapsed to spaces).  Formats each as ``- {slug}: {brief}``.

    Returns "(none yet)" if the concepts directory is missing or empty.
    """
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return "(none yet)"

    md_files = sorted(concepts_dir.glob("*.md"))
    if not md_files:
        return "(none yet)"

    lines: list[str] = []
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        brief = ""
        body = text
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm = text[:end + 3]
                body = text[end + 3:]
                for line in fm.split("\n"):
                    if line.startswith("brief:"):
                        brief = line[len("brief:"):].strip()
                        break
        if not brief:
            brief = body.strip().replace("\n", " ")[:150]
        if brief:
            lines.append(f"- {path.stem}: {brief}")

    return "\n".join(lines) or "(none yet)"


def _get_section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    """Return the [start, end) bounds for a Markdown H2 section."""
    for i, line in enumerate(lines):
        if line == heading:
            start = i + 1
            end = len(lines)
            for j in range(start, len(lines)):
                if lines[j].startswith("## "):
                    end = j
                    break
            return start, end
    return None


def _section_contains_link(lines: list[str], heading: str, link: str) -> bool:
    """Check whether an index entry already exists inside the named section."""
    bounds = _get_section_bounds(lines, heading)
    if bounds is None:
        return False

    start, end = bounds
    entry_prefix = f"- {link}"
    return any(line.startswith(entry_prefix) for line in lines[start:end])


def _replace_section_entry(lines: list[str], heading: str, link: str, entry: str) -> bool:
    """Replace the first matching entry within a specific section."""
    bounds = _get_section_bounds(lines, heading)
    if bounds is None:
        return False

    start, end = bounds
    entry_prefix = f"- {link}"
    for i in range(start, end):
        if lines[i].startswith(entry_prefix):
            lines[i] = entry
            return True
    return False


def _insert_section_entry(lines: list[str], heading: str, entry: str) -> bool:
    """Insert a new entry at the top of a specific section."""
    bounds = _get_section_bounds(lines, heading)
    if bounds is None:
        return False

    start, _ = bounds
    lines.insert(start, entry)
    return True



def _write_summary(wiki_dir: Path, doc_name: str, summary: str,
                    doc_type: str = "short") -> None:
    """Write summary page with frontmatter."""
    if summary.startswith("---"):
        end = summary.find("---", 3)
        if end != -1:
            summary = summary[end + 3:].lstrip("\n")
    summaries_dir = wiki_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    ext = "md" if doc_type == "short" else "json"
    fm_lines = [
        f"doc_type: {doc_type}",
        f"full_text: sources/{doc_name}.{ext}",
    ]
    frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
    (summaries_dir / f"{doc_name}.md").write_text(frontmatter + summary, encoding="utf-8")


_SAFE_NAME_RE = re.compile(r'[^\w\-]')
_CONCEPT_WIKILINK_RE = re.compile(r"\[\[concepts/([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")


def _sanitize_concept_name(name: str) -> str:
    """Sanitize a concept name for safe use as a filename."""
    name = unicodedata.normalize("NFKC", name)
    sanitized = _SAFE_NAME_RE.sub("-", name).strip("-")
    return sanitized or "unnamed-concept"


def _concept_alias_key(value: str) -> str:
    """Return a stable lookup key for a concept slug or title."""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\\", "/").strip()
    if value.startswith("concepts/"):
        value = value[len("concepts/"):]
    if value.endswith(".md"):
        value = value[:-3]
    return re.sub(r"[\s_\-]+", "", value).casefold()


def _extract_concept_link_targets(text: str) -> list[str]:
    """Return raw targets from [[concepts/...]] links."""
    return [match.group(1).strip() for match in _CONCEPT_WIKILINK_RE.finditer(text)]


def _build_concept_aliases(
    wiki_dir: Path,
    create_items: list[dict],
    update_items: list[dict],
    related_items: list[str],
) -> dict[str, str]:
    """Map concept titles, names, and existing stems to canonical slugs."""
    aliases: dict[str, str] = {}

    concepts_dir = wiki_dir / "concepts"
    if concepts_dir.exists():
        for path in concepts_dir.glob("*.md"):
            aliases[_concept_alias_key(path.stem)] = path.stem

    for item in create_items + update_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        slug = _sanitize_concept_name(name)
        aliases[_concept_alias_key(name)] = slug
        aliases[_concept_alias_key(slug)] = slug
        title = str(item.get("title", "")).strip()
        if title:
            aliases[_concept_alias_key(title)] = slug

    for related in related_items:
        slug = _sanitize_concept_name(str(related))
        aliases[_concept_alias_key(str(related))] = slug
        aliases[_concept_alias_key(slug)] = slug

    return aliases


def _planned_concept_slugs(
    create_items: list[dict],
    update_items: list[dict],
    related_items: list[str],
) -> set[str]:
    """Return canonical slugs covered by a concept plan."""
    slugs: set[str] = set()
    for item in create_items + update_items:
        if isinstance(item, dict) and item.get("name"):
            slugs.add(_sanitize_concept_name(str(item["name"])))
    for related in related_items:
        slugs.add(_sanitize_concept_name(str(related)))
    return slugs


def _normalize_concept_links(
    text: str,
    aliases: dict[str, str],
    allowed_slugs: set[str],
) -> str:
    """Rewrite known concept links to canonical slugs and unlink unknown ones."""
    def replace(match: re.Match) -> str:
        raw_target = match.group(1).strip()
        label = (match.group(2) or raw_target).strip()
        slug = aliases.get(_concept_alias_key(raw_target)) or aliases.get(raw_target)
        if slug is None:
            candidate = _sanitize_concept_name(raw_target)
            if candidate in allowed_slugs:
                slug = candidate
        if slug in allowed_slugs:
            return f"[[concepts/{slug}]]"
        return label

    return _CONCEPT_WIKILINK_RE.sub(replace, text)


def _ensure_summary_links_in_plan(wiki_dir: Path, summary: str, plan: dict) -> dict:
    """Ensure every summary concept link is represented by create/update/related."""
    normalized = {
        "create": list(plan.get("create", [])),
        "update": list(plan.get("update", [])),
        "related": list(plan.get("related", [])),
    }
    aliases = _build_concept_aliases(
        wiki_dir,
        normalized["create"],
        normalized["update"],
        normalized["related"],
    )
    planned = _planned_concept_slugs(
        normalized["create"],
        normalized["update"],
        normalized["related"],
    )

    for target in _extract_concept_link_targets(summary):
        key = _concept_alias_key(target)
        existing_slug = aliases.get(key)
        if existing_slug in planned:
            continue
        slug = _sanitize_concept_name(target)
        if slug in planned:
            continue
        normalized["create"].append({"name": slug, "title": target})
        aliases[_concept_alias_key(target)] = slug
        aliases[_concept_alias_key(slug)] = slug
        planned.add(slug)

    return normalized


def _rewrite_summary_links(
    wiki_dir: Path,
    doc_name: str,
    aliases: dict[str, str],
    allowed_slugs: set[str],
) -> None:
    """Normalize concept wikilinks in an already-written summary page."""
    summary_path = wiki_dir / "summaries" / f"{doc_name}.md"
    if not summary_path.exists():
        return
    text = summary_path.read_text(encoding="utf-8")
    normalized = _normalize_concept_links(text, aliases, allowed_slugs)
    if normalized != text:
        summary_path.write_text(normalized, encoding="utf-8")


def _write_concept(wiki_dir: Path, name: str, content: str, source_file: str, is_update: bool, brief: str = "") -> None:
    """Write or update a concept page, managing the sources frontmatter."""
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_concept_name(name)
    path = (concepts_dir / f"{safe_name}.md").resolve()
    if not path.is_relative_to(concepts_dir.resolve()):
        logger.warning("Concept name escapes concepts dir: %s", name)
        return

    if is_update and path.exists():
        existing = path.read_text(encoding="utf-8")
        if source_file not in existing:
            if existing.startswith("---"):
                end = existing.find("---", 3)
                if end != -1:
                    fm = existing[:end + 3]
                    body = existing[end + 3:]
                    if "sources:" in fm:
                        fm = fm.replace("sources: [", f"sources: [{source_file}, ")
                    else:
                        fm = fm.replace("---\n", f"---\nsources: [{source_file}]\n", 1)
                    existing = fm + body
            else:
                existing = f"---\nsources: [{source_file}]\n---\n\n" + existing
        # Strip frontmatter from LLM content to avoid duplicate blocks
        clean = content
        if clean.startswith("---"):
            end = clean.find("---", 3)
            if end != -1:
                clean = clean[end + 3:].lstrip("\n")
        # Replace body with LLM rewrite (prompt asks for full rewrite, not delta)
        if existing.startswith("---"):
            end = existing.find("---", 3)
            if end != -1:
                existing = existing[:end + 3] + "\n\n" + clean
            else:
                existing = clean
        else:
            existing = clean
        if brief and existing.startswith("---"):
            end = existing.find("---", 3)
            if end != -1:
                fm = existing[:end + 3]
                body = existing[end + 3:]
                if "brief:" in fm:
                    fm = re.sub(r"brief:.*", f"brief: {brief}", fm)
                else:
                    fm = fm.replace("---\n", f"---\nbrief: {brief}\n", 1)
                existing = fm + body
        path.write_text(existing, encoding="utf-8")
    else:
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].lstrip("\n")
        fm_lines = [f"sources: [{source_file}]"]
        if brief:
            fm_lines.append(f"brief: {brief}")
        frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
        path.write_text(frontmatter + content, encoding="utf-8")


def _add_related_link(wiki_dir: Path, concept_slug: str, doc_name: str, source_file: str) -> None:
    """Add a cross-reference link to an existing concept page (no LLM call)."""
    concepts_dir = wiki_dir / "concepts"
    path = concepts_dir / f"{concept_slug}.md"
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    link = f"[[summaries/{doc_name}]]"
    if link in text:
        return

    # Update sources in frontmatter
    if source_file not in text:
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm = text[:end + 3]
                body = text[end + 3:]
                if "sources:" in fm:
                    fm = fm.replace("sources: [", f"sources: [{source_file}, ")
                else:
                    fm = fm.replace("---\n", f"---\nsources: [{source_file}]\n", 1)
                text = fm + body
        else:
            text = f"---\nsources: [{source_file}]\n---\n\n" + text

    text += f"\n\nSee also: {link}"
    path.write_text(text, encoding="utf-8")


def _backlink_summary(wiki_dir: Path, doc_name: str, concept_slugs: list[str]) -> None:
    """Append missing concept wikilinks to the summary page (no LLM call).

    After all concepts are generated, this ensures the summary page links
    back to every related concept — closing the bidirectional link that
    concept pages already have toward the summary.

    If a ``## Related Concepts`` section already exists, new links are
    appended into it rather than creating a duplicate section.
    """
    summary_path = wiki_dir / "summaries" / f"{doc_name}.md"
    if not summary_path.exists():
        return

    text = summary_path.read_text(encoding="utf-8")
    missing = [slug for slug in concept_slugs if f"[[concepts/{slug}]]" not in text]
    if not missing:
        return

    new_links = "\n".join(f"- [[concepts/{s}]]" for s in missing)
    if "## Related Concepts" in text:
        # Append into existing section
        text = text.replace("## Related Concepts\n", f"## Related Concepts\n{new_links}\n", 1)
    else:
        text += f"\n\n## Related Concepts\n{new_links}\n"
    summary_path.write_text(text, encoding="utf-8")


def _backlink_concepts(wiki_dir: Path, doc_name: str, concept_slugs: list[str]) -> None:
    """Append missing summary wikilink to each concept page (no LLM call).

    Ensures every concept page links back to the source document's summary,
    regardless of whether the LLM included the link in its output.

    If a ``## Related Documents`` section already exists, the link is
    appended into it rather than creating a duplicate section.
    """
    link = f"[[summaries/{doc_name}]]"
    concepts_dir = wiki_dir / "concepts"

    for slug in concept_slugs:
        path = concepts_dir / f"{slug}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if link in text:
            continue
        if "## Related Documents" in text:
            text = text.replace("## Related Documents\n", f"## Related Documents\n- {link}\n", 1)
        else:
            text += f"\n\n## Related Documents\n- {link}\n"
        path.write_text(text, encoding="utf-8")

def _update_index(
    wiki_dir: Path, doc_name: str, concept_names: list[str],
    doc_brief: str = "", concept_briefs: dict[str, str] | None = None,
    doc_type: str = "short",
) -> None:
    """Append document and concept entries to index.md.

    When ``doc_brief`` or entries in ``concept_briefs`` are provided, entries
    are written as ``- [[link]] (type) - brief text``. Existing entries are
    detected within their own section by exact entry prefix and skipped to
    avoid duplicates.
    ``doc_type`` is ``"short"`` or ``"pageindex"`` - shown in the entry so the
    query agent knows how to access detailed content.
    """
    if concept_briefs is None:
        concept_briefs = {}

    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )

    lines = index_path.read_text(encoding="utf-8").split("\n")

    doc_link = f"[[summaries/{doc_name}]]"
    if not _section_contains_link(lines, "## Documents", doc_link):
        doc_entry = f"- {doc_link} ({doc_type})"
        if doc_brief:
            doc_entry += f" - {doc_brief}"
        _insert_section_entry(lines, "## Documents", doc_entry)

    for name in concept_names:
        concept_link = f"[[concepts/{name}]]"
        concept_entry = f"- {concept_link}"
        if name in concept_briefs:
            concept_entry += f" - {concept_briefs[name]}"
        if _section_contains_link(lines, "## Concepts", concept_link):
            if name in concept_briefs:
                _replace_section_entry(lines, "## Concepts", concept_link, concept_entry)
        else:
            _insert_section_entry(lines, "## Concepts", concept_entry)

    index_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DEFAULT_COMPILE_CONCURRENCY = 5
DEFAULT_LOCAL_LONG_PAGE_CHARS = 1800
DEFAULT_LOCAL_LONG_TOTAL_CHARS = 65000


def _build_local_long_doc_context(
    source_path: Path,
    page_chars: int = DEFAULT_LOCAL_LONG_PAGE_CHARS,
    total_chars: int = DEFAULT_LOCAL_LONG_TOTAL_CHARS,
) -> str:
    """Build a compact page-indexed prompt context from local long-doc JSON."""
    pages = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(pages, list):
        raise ValueError("Local long document source must be a JSON array of pages.")

    parts: list[str] = []
    used = 0
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_num = page.get("page", "?")
        content = str(page.get("content", "")).strip()
        if len(content) > page_chars:
            content = content[:page_chars].rstrip() + "\n[truncated]"
        images = page.get("images", [])
        image_lines = ""
        if isinstance(images, list) and images:
            image_paths = []
            for image in images[:6]:
                if isinstance(image, dict) and image.get("path"):
                    image_paths.append(str(image["path"]))
            if image_paths:
                image_lines = "\nImages: " + ", ".join(image_paths)
        block = f"## Page {page_num}\n{content}{image_lines}\n"
        if used + len(block) > total_chars:
            parts.append("[Further pages omitted from prompt context.]")
            break
        parts.append(block)
        used += len(block)

    return "\n".join(parts)


async def _compile_concepts(
    wiki_dir: Path,
    kb_dir: Path,
    model: str,
    system_msg: dict,
    doc_msg: dict,
    summary: str,
    doc_name: str,
    max_concurrency: int,
    doc_brief: str = "",
    doc_type: str = "short",
) -> None:
    """Shared Steps 2-4: concepts plan → generate/update → index.

    Uses ``_CONCEPTS_PLAN_USER`` to get a plan with create/update/related
    actions, then executes each action type accordingly.
    """
    source_file = f"summaries/{doc_name}.md"

    # --- Step 2: Get concepts plan (A cached) ---
    concept_briefs = _read_concept_briefs(wiki_dir)

    plan_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        {"role": "assistant", "content": summary},
        {"role": "user", "content": _CONCEPTS_PLAN_USER.format(
            concept_briefs=concept_briefs,
        )},
    ], "concepts-plan", max_tokens=1024)

    try:
        parsed = _parse_json(plan_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse concepts plan: %s", exc)
        logger.debug("Raw: %s", plan_raw)
        _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    # Fallback: if LLM returns a flat list, treat all items as "create"
    if isinstance(parsed, list):
        plan = {"create": parsed, "update": [], "related": []}
    else:
        plan = {
            "create": parsed.get("create", []),
            "update": parsed.get("update", []),
            "related": parsed.get("related", []),
        }

    plan = _ensure_summary_links_in_plan(wiki_dir, summary, plan)
    create_items = plan["create"]
    update_items = plan["update"]
    related_items = plan["related"]
    concept_aliases = _build_concept_aliases(wiki_dir, create_items, update_items, related_items)
    allowed_concept_slugs = _planned_concept_slugs(create_items, update_items, related_items)
    summary = _normalize_concept_links(summary, concept_aliases, allowed_concept_slugs)
    _rewrite_summary_links(wiki_dir, doc_name, concept_aliases, allowed_concept_slugs)

    if not create_items and not update_items and not related_items:
        _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    # --- Step 3: Generate/update concept pages concurrently (A cached) ---
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _gen_create(concept: dict) -> tuple[str, str, bool, str]:
        name = concept["name"]
        title = concept.get("title", name)
        async with semaphore:
            raw = await _llm_call_async(model, [
                system_msg,
                doc_msg,
                {"role": "assistant", "content": summary},
                {"role": "user", "content": _CONCEPT_PAGE_USER.format(
                    title=title, doc_name=doc_name,
                    update_instruction="",
                )},
            ], f"concept: {name}")
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            content = parsed.get("content", raw)
        except (json.JSONDecodeError, ValueError):
            brief, content = "", raw
        return name, content, False, brief

    async def _gen_update(concept: dict) -> tuple[str, str, bool, str]:
        name = concept["name"]
        title = concept.get("title", name)
        concept_path = wiki_dir / "concepts" / f"{_sanitize_concept_name(name)}.md"
        if concept_path.exists():
            raw_text = concept_path.read_text(encoding="utf-8")
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                existing_content = parts[2].strip() if len(parts) >= 3 else raw_text
            else:
                existing_content = raw_text
        else:
            existing_content = "(page not found — create from scratch)"
        async with semaphore:
            raw = await _llm_call_async(model, [
                system_msg,
                doc_msg,
                {"role": "assistant", "content": summary},
                {"role": "user", "content": _CONCEPT_UPDATE_USER.format(
                    title=title, doc_name=doc_name,
                    existing_content=existing_content,
                )},
            ], f"update: {name}")
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            content = parsed.get("content", raw)
        except (json.JSONDecodeError, ValueError):
            brief, content = "", raw
        return name, content, True, brief

    tasks = []
    tasks.extend(_gen_create(c) for c in create_items)
    tasks.extend(_gen_update(c) for c in update_items)

    concept_names: list[str] = []
    concept_briefs_map: dict[str, str] = {}

    if tasks:
        total = len(tasks)
        sys.stdout.write(f"    Generating {total} concept(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                logger.warning("Concept generation failed: %s", r)
                continue
            name, page_content, is_update, brief = r
            page_content = _normalize_concept_links(
                page_content,
                concept_aliases,
                allowed_concept_slugs,
            )
            _write_concept(wiki_dir, name, page_content, source_file, is_update, brief=brief)
            safe_name = _sanitize_concept_name(name)
            concept_names.append(safe_name)
            if brief:
                concept_briefs_map[safe_name] = brief

    # --- Step 3b: Process related items (code only, no LLM) ---
    sanitized_related = [_sanitize_concept_name(s) for s in related_items]
    for slug in sanitized_related:
        _add_related_link(wiki_dir, slug, doc_name, source_file)

    # --- Step 3c: Backlink — summary ↔ concepts (code only) ---
    all_concept_slugs = concept_names + sanitized_related
    if all_concept_slugs:
        _backlink_summary(wiki_dir, doc_name, all_concept_slugs)
        _backlink_concepts(wiki_dir, doc_name, all_concept_slugs)

    # --- Step 4: Update index (code only) ---
    _update_index(wiki_dir, doc_name, concept_names,
                  doc_brief=doc_brief, concept_briefs=concept_briefs_map,
                  doc_type=doc_type)


async def compile_short_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a short document using a multi-step LLM pipeline with caching.

    Step 1: Build base context A (schema + doc content), generate summary.
    Steps 2-4: Delegated to ``_compile_concepts``.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    content = source_path.read_text(encoding="utf-8")

    # Base context A: system + document
    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _SUMMARY_USER.format(
        doc_name=doc_name, content=content,
    )}

    # --- Step 1: Generate summary ---
    summary_raw = _llm_call(model, [system_msg, doc_msg], "summary")
    try:
        summary_parsed = _parse_json(summary_raw)
        doc_brief = summary_parsed.get("brief", "")
        summary = summary_parsed.get("content", summary_raw)
    except (json.JSONDecodeError, ValueError):
        doc_brief = ""
        summary = summary_raw
    _write_summary(wiki_dir, doc_name, summary)

    # --- Steps 2-4: Concept plan → generate/update → index ---
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg,
        summary, doc_name, max_concurrency, doc_brief=doc_brief,
        doc_type="short",
    )


async def compile_local_long_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a long PDF converted to local page JSON."""
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    content = _build_local_long_doc_context(source_path)

    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _LOCAL_LONG_DOC_SUMMARY_USER.format(
        doc_name=doc_name, content=content,
    )}

    summary_raw = _llm_call(model, [system_msg, doc_msg], "local-long-summary")
    try:
        summary_parsed = _parse_json(summary_raw)
        doc_brief = summary_parsed.get("brief", "")
        summary = summary_parsed.get("content", summary_raw)
    except (json.JSONDecodeError, ValueError):
        doc_brief = ""
        summary = summary_raw
    _write_summary(wiki_dir, doc_name, summary, doc_type="local-long")

    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg,
        summary, doc_name, max_concurrency, doc_brief=doc_brief,
        doc_type="local-long",
    )


async def compile_long_doc(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    model: str,
    doc_description: str = "",
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a long (PageIndex) document's concepts and index.

    The summary page is already written by the indexer. This function
    generates concept pages and updates the index.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    summary_content = summary_path.read_text(encoding="utf-8")

    # Base context A
    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _LONG_DOC_SUMMARY_USER.format(
        doc_name=doc_name, doc_id=doc_id, content=summary_content,
    )}

    # --- Step 1: Generate overview ---
    overview = _llm_call(model, [system_msg, doc_msg], "overview")

    # --- Steps 2-4: Concept plan → generate/update → index ---
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg,
        overview, doc_name, max_concurrency, doc_brief=doc_description,
        doc_type="pageindex",
    )
