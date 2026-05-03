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
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

import litellm

from openkb.agent.evidence import update_evidence_map
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


_COMPANIES_PLAN_USER = """\
Based on the summary above, decide which company-specific investment pages
should be created or updated.

Existing company pages:
{company_briefs}

Return a JSON object with one key:

1. "companies" - public companies or clearly named investable businesses with
material evidence in this document. Array of objects:
   {{"name": "company-slug", "title": "Human-Readable Company Name", "action": "create"}}

Rules:
- Include only company-specific entities with investment relevance, such as
  ratings, target prices, valuation context, AI exposure, catalysts, risks, or
  supply-chain position.
- Do NOT include products, technologies, countries, markets, concepts, or broad
  industry themes; those belong in `concepts/`.
- Prefer 3-8 high-signal companies when the report supports it.
- Use "action": "update" for an existing company page and "create" otherwise.
- If the report does not contain material company evidence, return
  {{"companies": []}}.

Return ONLY valid JSON, no fences, no explanation.
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

_COMPANY_PAGE_USER = """\
Write the company page for: {title}

This company relates to the document "{doc_name}" summarized above.
{update_instruction}

Structure the page for long-term investment research: business role in the
report, rating / target price / valuation context when available, AI or industry
exposure, forecasts and key numbers, catalysts, risks / bear-case evidence,
monitoring indicators, source evidence, related concepts, and
[[summaries/{doc_name}]].

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing this company's
  investment relevance in this document
- "content": The full company page in Markdown. Use [[concepts/...]] for
  reusable concepts and [[summaries/{doc_name}]] for the source summary.

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


def _is_compile_managed_wiki_path(relative_path: Path) -> bool:
    rel = relative_path.as_posix()
    return (
        rel in {"index.md", "evidence_map.json"}
        or rel.startswith("summaries/")
        or rel.startswith("companies/")
        or rel.startswith("concepts/")
    )


def _sync_staged_wiki(staged_wiki: Path, wiki_dir: Path) -> None:
    """Copy compile-managed staged wiki changes into the real wiki."""
    staged_files = {
        path.relative_to(staged_wiki)
        for path in staged_wiki.rglob("*")
        if path.is_file() and _is_compile_managed_wiki_path(path.relative_to(staged_wiki))
    }

    if wiki_dir.exists():
        for path in sorted(wiki_dir.rglob("*"), reverse=True):
            if not path.is_file():
                continue
            rel = path.relative_to(wiki_dir)
            if _is_compile_managed_wiki_path(rel) and rel not in staged_files:
                path.unlink()
    else:
        wiki_dir.mkdir(parents=True, exist_ok=True)

    for rel in sorted(staged_files):
        src = staged_wiki / rel
        dest = wiki_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() == src.read_bytes():
            continue
        shutil.copy2(src, dest)


async def _run_with_staged_wiki(kb_dir: Path, operation) -> None:
    """Run a compile operation against a staged wiki and commit on success."""
    wiki_dir = kb_dir / "wiki"
    staging_parent = kb_dir / ".openkb" / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="compile-", dir=staging_parent))
    staged_wiki = staging_root / "wiki"
    try:
        if wiki_dir.exists():
            shutil.copytree(wiki_dir, staged_wiki)
        else:
            staged_wiki.mkdir(parents=True, exist_ok=True)
        await operation(staged_wiki)
        _sync_staged_wiki(staged_wiki, wiki_dir)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _read_page_briefs(wiki_dir: Path, subdir: str) -> str:
    """Read existing wiki pages in a subdirectory as compact one-line summaries.

    For each page, reads the ``brief:`` field from YAML frontmatter if
    present; otherwise falls back to truncating the first 150 chars of the body
    (newlines collapsed to spaces).  Formats each as ``- {slug}: {brief}``.

    Returns "(none yet)" if the directory is missing or empty.
    """
    pages_dir = wiki_dir / subdir
    if not pages_dir.exists():
        return "(none yet)"

    md_files = sorted(pages_dir.glob("*.md"))
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


def _read_concept_briefs(wiki_dir: Path) -> str:
    """Read existing concept pages and return compact one-line summaries."""
    return _read_page_briefs(wiki_dir, "concepts")


def _read_company_briefs(wiki_dir: Path) -> str:
    """Read existing company pages and return compact one-line summaries."""
    return _read_page_briefs(wiki_dir, "companies")


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


def _ensure_index_section(lines: list[str], heading: str, before_heading: str | None = None) -> None:
    """Ensure an H2 index section exists, inserting before another H2 if possible."""
    if _get_section_bounds(lines, heading) is not None:
        return

    insert_at = len(lines)
    if before_heading is not None:
        for i, line in enumerate(lines):
            if line == before_heading:
                insert_at = i
                break

    block = [heading, ""]
    if insert_at > 0 and lines[insert_at - 1] != "":
        block.insert(0, "")
    if insert_at < len(lines) and lines[insert_at] != "":
        block.append("")
    lines[insert_at:insert_at] = block



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
    summary_text = frontmatter + summary
    (summaries_dir / f"{doc_name}.md").write_text(summary_text, encoding="utf-8")
    _record_summary_page_evidence(
        wiki_dir,
        doc_name,
        summary_text,
        f"sources/{doc_name}.{ext}",
    )


_SAFE_NAME_RE = re.compile(r'[^\w\-]')
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_CONCEPT_WIKILINK_RE = re.compile(r"\[\[concepts/([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
DEFAULT_SUMMARY_LINK_FALLBACK_LIMIT = 8


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


_COMPANY_FALLBACK_SIGNAL_RE = re.compile(
    r"首选|受益|看好|谨慎|Overweight|Underweight|Equal-Weight|Top Pick",
    re.IGNORECASE,
)
_COMPANY_RATING_TERMS = {
    "top pick",
    "overweight",
    "equal-weight",
    "underweight",
    "attractive",
}
_COMPANY_EXCLUDED_NAMES = {
    "AI",
    "GPU",
    "CPU",
    "ASIC",
    "HBM",
    "CPO",
    "CoWoS",
    "SoIC",
    "CSP",
    "P/E",
    "P/B",
    "EBITDA",
    "CAGR",
    "Top Pick",
    "Overweight",
    "Equal-Weight",
    "Underweight",
    "Attractive",
}


def _clean_company_fragment(value: str) -> str:
    """Normalize a possible company fragment from an investment summary line."""
    value = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1), value)
    value = re.sub(r"\*\*|__|`", "", value)
    value = value.strip(" \t\r\n-*•:：,，;；.。、")
    return value.strip()


def _is_rating_term(value: str) -> bool:
    key = value.strip().casefold()
    return key in _COMPANY_RATING_TERMS or any(term in key for term in _COMPANY_RATING_TERMS)


def _looks_like_company_name(value: str) -> bool:
    value = _clean_company_fragment(value)
    if not value or value in _COMPANY_EXCLUDED_NAMES or _is_rating_term(value):
        return False
    if len(value) > 48:
        return False
    if re.search(r"\d{4}|CAGR|P/[EB]|EBITDA|kwpm|Gb", value, re.IGNORECASE):
        return False
    return bool(re.search(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}", value))


def _company_item_from_fragment(fragment: str) -> dict[str, str] | None:
    """Convert one split summary fragment into a company plan item."""
    fragment = _clean_company_fragment(fragment)
    if not fragment:
        return None

    paren = re.match(r"(?P<outer>.+?)[（(](?P<inner>[^）)]+)[）)]", fragment)
    if paren:
        outer = _clean_company_fragment(paren.group("outer"))
        inner = _clean_company_fragment(paren.group("inner"))
        inner_primary = _clean_company_fragment(re.split(r"[,，;/]", inner, 1)[0])
        if inner_primary and not _is_rating_term(inner_primary):
            if _looks_like_company_name(inner_primary):
                title = f"{outer} ({inner_primary})" if outer else inner_primary
                return {"name": inner_primary, "title": title, "action": "create"}
        if _looks_like_company_name(outer):
            return {"name": outer, "title": outer, "action": "create"}
        return None

    if _looks_like_company_name(fragment):
        return {"name": fragment, "title": fragment, "action": "create"}
    return None


def _extract_company_candidates_from_summary(summary: str, max_companies: int = 12) -> list[dict[str, str]]:
    """Fallback extraction of high-signal company names from investment summaries."""
    if summary.startswith("---"):
        end = summary.find("---", 3)
        if end != -1:
            summary = summary[end + 3:].lstrip("\n")

    companies: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in summary.splitlines():
        if not _COMPANY_FALLBACK_SIGNAL_RE.search(line):
            continue
        if "：" in line:
            line = line.split("：", 1)[1]
        elif ":" in line:
            line = line.split(":", 1)[1]
        for fragment in re.split(r"[、;；]", line):
            item = _company_item_from_fragment(fragment)
            if item is None:
                continue
            key = _concept_alias_key(item["name"])
            if key in seen:
                continue
            seen.add(key)
            companies.append(item)
            if len(companies) >= max_companies:
                return companies
    return companies


_CONCEPT_FALLBACK_PATTERNS: list[tuple[str, str, str]] = [
    (r"云.*资本支出|Cloud\s*CAPEX|CSP", "Cloud_CAPEX", "Cloud CAPEX"),
    (r"AI\s*CPU|Grace\s*CPU", "AI_CPU", "AI CPU"),
    (r"全球.*AI\s*GPU|global.*AI\s*GPU|AI\s*GPU.*路线图|GB\d{3,4}|Rubin", "AI_GPU", "AI GPU"),
    (r"先进封装|Advanced\s*Packaging|CoWoS|SoIC", "Advanced_Packaging", "Advanced Packaging"),
    (r"CoWoS", "CoWoS", "CoWoS"),
    (r"SoIC", "SoIC", "SoIC"),
    (r"AI\s*ASIC|定制芯片|定制化", "AI_ASIC", "AI ASIC"),
    (r"HBM|高带宽内存", "HBM", "HBM"),
    (r"NOR|Flash", "NOR_Flash", "NOR Flash"),
    (r"中国.*AI.*芯片|国产.*AI.*芯片|China.*AI.*GPU", "China_AI_GPU", "China AI GPU"),
    (r"测试设备|测试耗材|Semiconductor\s*Testing|探针|测试插座", "Semiconductor_Testing", "Semiconductor Testing"),
    (r"CPO|共封装光学", "CPO", "CPO"),
    (r"光引擎|光芯片|Optical", "Optical_Engines", "Optical Engines"),
    (r"出口管制|地缘|Export\s*Control", "Export_Controls", "Export Controls"),
    (r"半导体景气|景气周期|cycle", "Semiconductor_Cycle", "Semiconductor Cycle"),
]


def _extract_concept_candidates_from_summary(summary: str, max_concepts: int = 10) -> list[dict[str, str]]:
    """Fallback extraction of durable investment concepts from summary headings."""
    if summary.startswith("---"):
        end = summary.find("---", 3)
        if end != -1:
            summary = summary[end + 3:].lstrip("\n")

    concepts: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern, name, title in _CONCEPT_FALLBACK_PATTERNS:
        if not re.search(pattern, summary, re.IGNORECASE):
            continue
        slug = _sanitize_concept_name(name)
        if slug in seen:
            continue
        seen.add(slug)
        concepts.append({"name": slug, "title": title})
        if len(concepts) >= max_concepts:
            return concepts
    return concepts


_CONCEPT_PLAN_NAME_ALIASES = {
    "asic": ("AI_ASIC", "AI ASIC"),
    "aiasic": ("AI_ASIC", "AI ASIC"),
    "ai定制芯片asic": ("AI_ASIC", "AI ASIC"),
    "定制芯片": ("AI_ASIC", "AI ASIC"),
}


def _company_alias_keys(company_items: list[dict]) -> set[str]:
    """Return normalized aliases for companies that must not become concepts."""
    keys: set[str] = set()
    for item in company_items:
        for field in ("name", "title"):
            value = str(item.get(field, "")).strip()
            if value:
                keys.add(_concept_alias_key(value))
                paren = re.search(r"[（(]([^）)]+)[）)]", value)
                if paren:
                    keys.add(_concept_alias_key(paren.group(1)))
    return keys


def _canonicalize_concept_item(item: dict) -> dict | None:
    """Normalize concept plan items to canonical durable slugs."""
    name = str(item.get("name", "")).strip()
    if not name:
        return None
    title = str(item.get("title", name)).strip() or name
    alias = _CONCEPT_PLAN_NAME_ALIASES.get(_concept_alias_key(name))
    if alias is not None:
        name, title = alias
    return {"name": _sanitize_concept_name(name), "title": title}


def _filter_concept_plan_against_companies(plan: dict, company_keys: set[str]) -> dict:
    """Remove company names from concept plan and canonicalize remaining concepts."""
    filtered = {"create": [], "update": [], "related": []}

    for action in ("create", "update"):
        for item in plan.get(action, []):
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name", "")).strip()
            raw_title = str(item.get("title", raw_name)).strip()
            if _concept_alias_key(raw_name) in company_keys or _concept_alias_key(raw_title) in company_keys:
                continue
            canonical = _canonicalize_concept_item(item)
            if canonical is not None:
                filtered[action].append(canonical)

    for related in plan.get("related", []):
        raw = str(related).strip()
        if raw and _concept_alias_key(raw) not in company_keys:
            alias = _CONCEPT_PLAN_NAME_ALIASES.get(_concept_alias_key(raw))
            filtered["related"].append(alias[0] if alias else raw)

    return filtered


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


def _known_wiki_pages(wiki_dir: Path) -> set[str]:
    """Return existing wiki page IDs without .md suffix."""
    if not wiki_dir.exists():
        return set()
    return {
        str(path.relative_to(wiki_dir)).replace("\\", "/")[:-3]
        for path in wiki_dir.rglob("*.md")
    }


def _normalize_wiki_links(
    text: str,
    aliases: dict[str, str],
    allowed_slugs: set[str],
    valid_pages: set[str],
) -> str:
    """Normalize concept links and unlink unresolved bare wiki links."""
    def replace(match: re.Match) -> str:
        raw_target = match.group(1).strip().replace("\\", "/")
        label = (match.group(2) or raw_target).strip()

        slug = aliases.get(_concept_alias_key(raw_target))
        if slug in allowed_slugs:
            return f"[[concepts/{slug}]]"

        if raw_target.startswith("concepts/"):
            candidate = _sanitize_concept_name(raw_target[len("concepts/"):])
            if candidate in allowed_slugs:
                return f"[[concepts/{candidate}]]"
            return label

        if raw_target in valid_pages:
            if match.group(2):
                return f"[[{raw_target}|{label}]]"
            return f"[[{raw_target}]]"

        return label

    return _WIKILINK_RE.sub(replace, text)


def _ensure_summary_links_in_plan(
    wiki_dir: Path,
    summary: str,
    plan: dict,
    max_new_links: int = DEFAULT_SUMMARY_LINK_FALLBACK_LIMIT,
) -> dict:
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

    missing_targets: list[str] = []
    seen_missing: set[str] = set()
    for target in _extract_concept_link_targets(summary):
        key = _concept_alias_key(target)
        existing_slug = aliases.get(key)
        if existing_slug in planned:
            continue
        slug = _sanitize_concept_name(target)
        if slug in planned:
            continue
        if key in seen_missing:
            continue
        missing_targets.append(target)
        seen_missing.add(key)

    if len(missing_targets) > max_new_links:
        logger.warning(
            "Summary contains %d unplanned concept links; unlinking them instead of "
            "creating a noisy concept batch.",
            len(missing_targets),
        )
        return normalized

    for target in missing_targets:
        slug = _sanitize_concept_name(target)
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
    valid_pages: set[str],
) -> None:
    """Normalize concept wikilinks in an already-written summary page."""
    summary_path = wiki_dir / "summaries" / f"{doc_name}.md"
    if not summary_path.exists():
        return
    text = summary_path.read_text(encoding="utf-8")
    normalized = _normalize_wiki_links(text, aliases, allowed_slugs, valid_pages)
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


def _write_company(wiki_dir: Path, name: str, content: str, source_file: str, is_update: bool, brief: str = "") -> None:
    """Write or update a company page, managing the sources frontmatter."""
    companies_dir = wiki_dir / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_concept_name(name)
    path = (companies_dir / f"{safe_name}.md").resolve()
    if not path.is_relative_to(companies_dir.resolve()):
        logger.warning("Company name escapes companies dir: %s", name)
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
        clean = content
        if clean.startswith("---"):
            end = clean.find("---", 3)
            if end != -1:
                clean = clean[end + 3:].lstrip("\n")
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


def _frontmatter_source_entries(text: str) -> list[str]:
    """Return source entries from a generated page frontmatter block."""
    if not text.startswith("---"):
        return []
    end = text.find("---", 3)
    if end == -1:
        return []
    frontmatter = text[:end + 3]
    match = re.search(r"^sources:\s*\[(.*?)\]\s*$", frontmatter, re.MULTILINE)
    if not match:
        return []
    return [
        item.strip()
        for item in match.group(1).split(",")
        if item.strip()
    ]


_GENERATED_PAGE_REF_RE = re.compile(r"\b(?:p\.?\s*|page\s+)(\d{1,4})\b", re.IGNORECASE)


def _strip_frontmatter_block(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3:].lstrip()


def _clean_evidence_snippet(line: str, limit: int = 220) -> str:
    snippet = " ".join(line.strip().split())
    snippet = re.sub(r"^[-*+]\s+", "", snippet)
    snippet = re.sub(r"^\d+[.)]\s+", "", snippet)
    snippet = re.sub(r"\[\[summaries/[^|\]]+(?:\|[^\]]+)?\]\]", "", snippet)
    snippet = _GENERATED_PAGE_REF_RE.sub("", snippet, count=1)
    snippet = snippet.lstrip(" :-")
    return snippet[:limit].rstrip()


def _extract_page_reference_evidence(
    content: str,
    source_file: str,
    source_link: str,
    *,
    max_items: int = 12,
) -> list[dict[str, str]]:
    """Extract page-reference evidence from generated Markdown content."""
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in _strip_frontmatter_block(content).splitlines():
        line = " ".join(raw_line.strip().split())
        if not line or line.startswith("#"):
            continue
        match = _GENERATED_PAGE_REF_RE.search(line)
        if not match:
            continue
        page = match.group(1)
        snippet = _clean_evidence_snippet(line)
        if not snippet:
            continue
        key = (page, snippet.casefold())
        if key in seen:
            continue
        seen.add(key)
        evidence.append({
            "source": source_file,
            "link": source_link,
            "page": page,
            "snippet": snippet,
        })
        if len(evidence) >= max_items:
            break
    return evidence


def _extract_generated_page_evidence(
    content: str,
    source_file: str,
    *,
    max_items: int = 12,
) -> list[dict[str, str]]:
    """Extract source/page evidence from a generated concept or company page."""
    if not source_file.startswith("summaries/"):
        return []

    source_link = source_file[:-3] if source_file.endswith(".md") else source_file
    return _extract_page_reference_evidence(
        content,
        source_file,
        source_link,
        max_items=max_items,
    )


def _record_summary_page_evidence(
    wiki_dir: Path,
    doc_name: str,
    content: str,
    source_file: str,
) -> None:
    evidence = _extract_page_reference_evidence(
        content,
        source_file,
        f"summaries/{doc_name}",
    )
    update_evidence_map(wiki_dir, f"summaries/{doc_name}.md", evidence)


def _record_generated_page_evidence(
    wiki_dir: Path,
    page_path: str,
    content: str,
    source_file: str,
) -> None:
    evidence = _extract_generated_page_evidence(content, source_file)
    update_evidence_map(wiki_dir, page_path, evidence)


def _remove_index_entries(wiki_dir: Path, page_ids: set[str]) -> None:
    """Remove index rows for deleted generated pages."""
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
        index_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")


def cleanup_generated_pages_for_source(wiki_dir: Path, doc_name: str) -> list[str]:
    """Delete generated company/concept pages whose only source is this document."""
    source_file = f"summaries/{doc_name}.md"
    removed: list[str] = []

    for subdir in ("companies", "concepts"):
        pages_dir = wiki_dir / subdir
        if not pages_dir.exists():
            continue
        for path in sorted(pages_dir.glob("*.md")):
            sources = _frontmatter_source_entries(path.read_text(encoding="utf-8"))
            if sources == [source_file]:
                page_id = f"{subdir}/{path.stem}"
                path.unlink()
                removed.append(page_id)

    _remove_index_entries(wiki_dir, set(removed))
    return removed


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
    company_names: list[str] | None = None,
    company_briefs: dict[str, str] | None = None,
    doc_type: str = "short",
) -> None:
    """Append document, company, and concept entries to index.md.

    When ``doc_brief`` or entries in ``concept_briefs`` are provided, entries
    are written as ``- [[link]] (type) - brief text``. Existing entries are
    detected within their own section by exact entry prefix and skipped to
    avoid duplicates.
    ``doc_type`` is ``"short"`` or ``"pageindex"`` - shown in the entry so the
    query agent knows how to access detailed content.
    """
    if concept_briefs is None:
        concept_briefs = {}
    if company_names is None:
        company_names = []
    if company_briefs is None:
        company_briefs = {}

    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "# Knowledge Base Index\n\n"
            "## Documents\n\n"
            "## Companies\n\n"
            "## Industries\n\n"
            "## Themes\n\n"
            "## Metrics\n\n"
            "## Risks\n\n"
            "## Concepts\n\n"
            "## Explorations\n",
            encoding="utf-8",
        )

    lines = index_path.read_text(encoding="utf-8").split("\n")
    _ensure_index_section(lines, "## Documents", before_heading="## Companies")
    _ensure_index_section(lines, "## Companies", before_heading="## Industries")
    _ensure_index_section(lines, "## Industries", before_heading="## Themes")
    _ensure_index_section(lines, "## Themes", before_heading="## Metrics")
    _ensure_index_section(lines, "## Metrics", before_heading="## Risks")
    _ensure_index_section(lines, "## Risks", before_heading="## Concepts")
    _ensure_index_section(lines, "## Concepts", before_heading="## Explorations")

    doc_link = f"[[summaries/{doc_name}]]"
    if not _section_contains_link(lines, "## Documents", doc_link):
        doc_entry = f"- {doc_link} ({doc_type})"
        if doc_brief:
            doc_entry += f" - {doc_brief}"
        _insert_section_entry(lines, "## Documents", doc_entry)

    for name in company_names:
        company_link = f"[[companies/{name}]]"
        company_entry = f"- {company_link}"
        if name in company_briefs:
            company_entry += f" - {company_briefs[name]}"
        if _section_contains_link(lines, "## Companies", company_link):
            if name in company_briefs:
                _replace_section_entry(lines, "## Companies", company_link, company_entry)
        else:
            _insert_section_entry(lines, "## Companies", company_entry)

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

    # --- Step 2a: Get company plan (A cached) ---
    company_briefs = _read_company_briefs(wiki_dir)
    company_plan_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        {"role": "assistant", "content": summary},
        {"role": "user", "content": _COMPANIES_PLAN_USER.format(
            company_briefs=company_briefs,
        )},
    ], "companies-plan", max_tokens=1024)

    try:
        company_parsed = _parse_json(company_plan_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse companies plan: %s", exc)
        logger.debug("Raw: %s", company_plan_raw)
        company_parsed = {
            "companies": _extract_company_candidates_from_summary(summary),
        }

    fallback_company_items = _extract_company_candidates_from_summary(summary)
    company_items: list[dict] = []
    if isinstance(company_parsed, dict):
        raw_company_items = company_parsed.get("companies", [])
        if isinstance(raw_company_items, list):
            company_items = [
                item for item in raw_company_items
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            ]
    if not company_items:
        company_items = fallback_company_items
    company_keys = _company_alias_keys(company_items + fallback_company_items)

    planned_company_slugs = {
        _sanitize_concept_name(str(item["name"]))
        for item in company_items
    }

    # --- Step 2b: Get concepts plan (A cached) ---
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
        parsed = {"create": [], "update": [], "related": []}

    # Fallback: if LLM returns a flat list, treat all items as "create"
    if isinstance(parsed, list):
        plan = {"create": parsed, "update": [], "related": []}
    else:
        plan = {
            "create": parsed.get("create", []),
            "update": parsed.get("update", []),
            "related": parsed.get("related", []),
        }

    summary_concept_targets = _extract_concept_link_targets(summary)
    plan = _filter_concept_plan_against_companies(plan, company_keys)
    plan = _ensure_summary_links_in_plan(wiki_dir, summary, plan)
    planned_after_filter = _planned_concept_slugs(plan["create"], plan["update"], plan["related"])
    if len(planned_after_filter) < 5 and not summary_concept_targets:
        for item in _extract_concept_candidates_from_summary(summary):
            slug = _sanitize_concept_name(str(item["name"]))
            if slug in planned_after_filter:
                continue
            if (wiki_dir / "concepts" / f"{slug}.md").exists():
                plan["update"].append(item)
            else:
                plan["create"].append(item)
            planned_after_filter.add(slug)
    create_items = plan["create"]
    update_items = plan["update"]
    related_items = plan["related"]
    concept_aliases = _build_concept_aliases(wiki_dir, create_items, update_items, related_items)
    allowed_concept_slugs = _planned_concept_slugs(create_items, update_items, related_items)
    valid_pages = (
        _known_wiki_pages(wiki_dir)
        | {f"companies/{slug}" for slug in planned_company_slugs}
        | {f"concepts/{slug}" for slug in allowed_concept_slugs}
        | {f"summaries/{doc_name}"}
    )
    summary = _normalize_wiki_links(summary, concept_aliases, allowed_concept_slugs, valid_pages)
    _rewrite_summary_links(wiki_dir, doc_name, concept_aliases, allowed_concept_slugs, valid_pages)

    if not company_items and not create_items and not update_items and not related_items:
        _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    # --- Step 3: Generate/update company and concept pages concurrently (A cached) ---
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _gen_company(company: dict) -> tuple[str, str, bool, str]:
        name = str(company["name"]).strip()
        title = str(company.get("title", name)).strip() or name
        safe_name = _sanitize_concept_name(name)
        company_path = wiki_dir / "companies" / f"{safe_name}.md"
        requested_action = str(company.get("action", "")).lower()
        is_update = requested_action == "update" or company_path.exists()
        if is_update and company_path.exists():
            raw_text = company_path.read_text(encoding="utf-8")
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                existing_content = parts[2].strip() if len(parts) >= 3 else raw_text
            else:
                existing_content = raw_text
            update_instruction = (
                "Current content of this company page:\n"
                f"{existing_content}\n\n"
                "Integrate the new document evidence naturally; do not just append."
            )
        else:
            update_instruction = ""
        async with semaphore:
            raw = await _llm_call_async(model, [
                system_msg,
                doc_msg,
                {"role": "assistant", "content": summary},
                {"role": "user", "content": _COMPANY_PAGE_USER.format(
                    title=title, doc_name=doc_name,
                    update_instruction=update_instruction,
                )},
            ], f"company: {name}")
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            content = parsed.get("content", raw)
        except (json.JSONDecodeError, ValueError):
            brief, content = "", raw
        return name, content, is_update, brief

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

    company_tasks = [_gen_company(c) for c in company_items]

    tasks = []
    tasks.extend(_gen_create(c) for c in create_items)
    tasks.extend(_gen_update(c) for c in update_items)

    company_names: list[str] = []
    company_briefs_map: dict[str, str] = {}
    concept_names: list[str] = []
    concept_briefs_map: dict[str, str] = {}

    if company_tasks:
        total = len(company_tasks)
        sys.stdout.write(f"    Generating {total} company page(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()

        company_results = await asyncio.gather(*company_tasks, return_exceptions=True)
        for r in company_results:
            if isinstance(r, Exception):
                logger.warning("Company generation failed: %s", r)
                continue
            name, page_content, is_update, brief = r
            page_content = _normalize_wiki_links(
                page_content,
                concept_aliases,
                allowed_concept_slugs,
                valid_pages,
            )
            _write_company(wiki_dir, name, page_content, source_file, is_update, brief=brief)
            safe_name = _sanitize_concept_name(name)
            company_names.append(safe_name)
            if brief:
                company_briefs_map[safe_name] = brief

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
            page_content = _normalize_wiki_links(
                page_content,
                concept_aliases,
                allowed_concept_slugs,
                valid_pages,
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
    successful_concept_slugs = set(concept_names + sanitized_related)
    final_valid_pages = _known_wiki_pages(wiki_dir) | {f"summaries/{doc_name}"}
    final_aliases = {
        key: slug
        for key, slug in concept_aliases.items()
        if slug in successful_concept_slugs
    }
    _rewrite_summary_links(
        wiki_dir,
        doc_name,
        final_aliases,
        successful_concept_slugs,
        final_valid_pages,
    )
    for slug in concept_names:
        concept_path = wiki_dir / "concepts" / f"{slug}.md"
        if not concept_path.exists():
            continue
        text = concept_path.read_text(encoding="utf-8")
        normalized_text = _normalize_wiki_links(
            text,
            final_aliases,
            successful_concept_slugs,
            final_valid_pages,
        )
        if normalized_text != text:
            concept_path.write_text(normalized_text, encoding="utf-8")
            text = normalized_text
        _record_generated_page_evidence(
            wiki_dir,
            f"concepts/{slug}.md",
            text,
            source_file,
        )
    for slug in company_names:
        company_path = wiki_dir / "companies" / f"{slug}.md"
        if not company_path.exists():
            continue
        text = company_path.read_text(encoding="utf-8")
        normalized_text = _normalize_wiki_links(
            text,
            final_aliases,
            successful_concept_slugs,
            final_valid_pages,
        )
        if normalized_text != text:
            company_path.write_text(normalized_text, encoding="utf-8")
            text = normalized_text
        _record_generated_page_evidence(
            wiki_dir,
            f"companies/{slug}.md",
            text,
            source_file,
        )

    all_concept_slugs = concept_names + sanitized_related
    if all_concept_slugs:
        _backlink_summary(wiki_dir, doc_name, all_concept_slugs)
        _backlink_concepts(wiki_dir, doc_name, all_concept_slugs)

    # --- Step 4: Update index (code only) ---
    _update_index(wiki_dir, doc_name, concept_names,
                  doc_brief=doc_brief, concept_briefs=concept_briefs_map,
                  company_names=company_names, company_briefs=company_briefs_map,
                  doc_type=doc_type)


async def _compile_short_doc_to_wiki(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    wiki_dir: Path,
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


async def compile_short_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a short document and commit generated wiki files atomically."""
    async def operation(staged_wiki: Path) -> None:
        await _compile_short_doc_to_wiki(
            doc_name,
            source_path,
            kb_dir,
            staged_wiki,
            model,
            max_concurrency,
        )

    await _run_with_staged_wiki(kb_dir, operation)


async def _compile_local_long_doc_to_wiki(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    wiki_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a long PDF converted to local page JSON."""
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

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


async def compile_local_long_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a local-long document and commit generated wiki files atomically."""
    async def operation(staged_wiki: Path) -> None:
        await _compile_local_long_doc_to_wiki(
            doc_name,
            source_path,
            kb_dir,
            staged_wiki,
            model,
            max_concurrency,
        )

    await _run_with_staged_wiki(kb_dir, operation)


async def _compile_long_doc_to_wiki(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    wiki_dir: Path,
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

    schema_md = get_agents_md(wiki_dir)
    real_wiki_dir = kb_dir / "wiki"
    try:
        staged_summary_path = wiki_dir / summary_path.resolve().relative_to(real_wiki_dir.resolve())
    except ValueError:
        staged_summary_path = summary_path
    summary_content = staged_summary_path.read_text(encoding="utf-8")

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


async def compile_long_doc(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    model: str,
    doc_description: str = "",
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a PageIndex long document and commit generated wiki files atomically."""
    async def operation(staged_wiki: Path) -> None:
        await _compile_long_doc_to_wiki(
            doc_name,
            summary_path,
            doc_id,
            kb_dir,
            staged_wiki,
            model,
            doc_description=doc_description,
            max_concurrency=max_concurrency,
        )

    await _run_with_staged_wiki(kb_dir, operation)
