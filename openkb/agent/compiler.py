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
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterator

import litellm

from openkb.agent.evidence import update_evidence_map
from openkb.entity_registry import EntityRegistry, ResolvedEntity
from openkb.schema import get_agents_md
from openkb.llm_runtime import acompletion, completion

logger = logging.getLogger(__name__)

CompileProgressCallback = Callable[[str], None]
_COMPILE_PROGRESS_CALLBACK: ContextVar[CompileProgressCallback | None] = ContextVar(
    "openkb_compile_progress_callback",
    default=None,
)


@contextmanager
def compile_progress_callback(callback: CompileProgressCallback | None) -> Iterator[None]:
    """Attach a progress callback to compiler LLM calls in the current context."""
    token = _COMPILE_PROGRESS_CALLBACK.set(callback)
    try:
        yield
    finally:
        _COMPILE_PROGRESS_CALLBACK.reset(token)


def _emit_compile_progress(message: str) -> None:
    callback = _COMPILE_PROGRESS_CALLBACK.get()
    if callback is not None:
        callback(message)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are OpenKB's wiki compilation agent for a **long-term personal investment
knowledge base**. The KB is built to be re-read by future-you and by other
LLMs as durable context for investment research.

{schema_md}

## Global principles (apply to every page you generate)
1. **Trustworthiness** — every material claim must be traceable back to the
   source document. Cite anchors: PDF → ``p.12``; video/audio transcript →
   ``[01:23:45]`` (or ``12:34``); chapter/section → ``§3.2`` or ``第3章``;
   if no anchor exists, paraphrase carefully and note ``(unverified)``.
2. **Modularity** — concept pages should be reusable across documents. Prefer
   short canonical Chinese nouns over long descriptive titles. A concept that
   only applies to one company or one event belongs in that company page or
   the document summary, not in ``concepts/``.
3. **Freshness via update** — when a similar page exists, prefer ``update``
   (merging new evidence) over ``create`` (spawning a near-duplicate). Use the
   summary's existing-pages list to avoid renaming the same idea.
4. **Investment focus** — favour content that survives multiple quarters:
   business model, capital allocation, durable risks, monitoring indicators,
   bear-case signals. Filter out short-lived narrative noise.

Write all content in {language} language.
Use [[wikilinks]] to connect related pages (e.g. ``[[concepts/护城河]]``).
"""

_SUMMARY_USER = """\
New document: {doc_name}

Full text:
{content}

Write a summary page for this **investment-research document** in Markdown.

Use this investment structure when applicable:
- Core thesis and conclusion
- Ratings / top ideas / company table when available
- Key numbers, assumptions, forecasts, and valuation context
- Industry chain map and bottlenecks
- Catalysts and monitoring indicators
- Risks, bear-case evidence, and disconfirming signals
- Source evidence section — anchor each evidence line. PDF: ``(p.12)``;
  video/audio: ``[01:23:45]`` or ``[12:34]``; transcript with section
  headings: ``(§核心论点)``; chapter book: ``(§3.2)`` or ``(第3章)``.
  If the source has no anchors at all (raw transcript), prefix evidence
  lines with the most relevant section/topic in ``(§...)``.

Keep all material claims traceable to the source text. Preserve important
numbers, dates, companies, and units.

Use ``[[concepts/...]]`` ONLY when the target idea deserves a durable
cross-document page. Heuristics for "deserves":
- Appears (or will appear) in ≥ 2 documents.
- Is a reusable noun (mechanism, metric, risk type, framework, policy,
  technology, monitoring indicator). NOT a one-off event or quarterly figure.
- Has a stable canonical Chinese name (no English slugs, no descriptive
  sentence-as-name).

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown. Start with ``# {doc_name}`` (or a
  cleaner Chinese title if the doc_name is a filename). Include key concepts,
  findings, ideas, and ``[[wikilinks]]`` to durable concepts.

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
   {{"name": "中文公司名", "title": "Human-Readable Company Name", "action": "create"}}

Rules:
- Include only company-specific entities with investment relevance, such as
  ratings, target prices, valuation context, AI exposure, catalysts, risks, or
  supply-chain position.
- The page subject must be an actual company, listed company, private company,
  subsidiary, or clearly named investable business entity.
- **Name format (CRITICAL)** — "name" MUST be a canonical company noun:
  * GOOD: ``腾讯``, ``小米集团``, ``中国长江电力``, ``比亚迪``, ``中天科技``, ``AMD``,
    ``Wuxi Biologics``, ``松下电器产业株式会社``.
  * BAD (these are research-fragment titles, NOT companies — never propose them):
    - Starts with digit + dash: ``1-柴油机毛利率持续提升``,
      ``2-中船柴油机剩余股权收购``, ``4月挖机销量有望超预期``
    - Contains event/finance verbs: ``业绩优秀但股价承压``,
      ``AI产业链算力需求爆发端``, ``东南亚户储需求恢复``
    - Contains data points: ``26Q1沿海港口集装箱吞吐量7918万TEU``,
      ``中微公司110倍-需谨慎看待估值合理性``
    - Long descriptive phrase (> 12 CJK chars without 公司/集团/股份/控股):
      ``光伏出口强劲及地缘冲突致全球提前补库``
- Do NOT include products, technologies, countries, markets, concepts, or broad
  industry themes; those belong in `industries/` only when they are real
  industries, otherwise in `concepts/`.
- **Update vs create** — before "create", check the existing list above. If a
  company with the same canonical name (allowing for 公司/集团/股份/有限 suffix
  variants) exists, use ``"action": "update"``.
- If uncertain whether a candidate is a real company, do not include it.
- Prefer 3-8 high-signal companies when the report supports it.
- Use a concise Chinese page filename in "name" when the KB language is
  Chinese. Do not use English slugs unless the company is genuinely
  English-only (e.g. ``AMD``, ``Wuxi Biologics``).
- If the report does not contain material company evidence, return
  {{"companies": []}}.

Return ONLY valid JSON, no fences, no explanation.
"""


_INVESTMENT_PAGES_PLAN_USER = """\
Based on the summary above, decide which dedicated investment pages should be
created or updated outside `companies/` and `concepts/`.

Existing industry pages:
{industry_briefs}

Return a JSON object with one key:

1. "industries" - sectors, industry structures, value chains, capacity cycles,
   bottlenecks, and competitive maps. Array of objects:
   {{"name": "中文行业文件名", "title": "Human-Readable Industry", "action": "create"}}

Rules:
- Use "action": "update" for an existing page and "create" otherwise.
- **Variant detection (CRITICAL)** — before "create", scan existing industry
  briefs for the same sector under a different name. The following are all
  DUPLICATES and should resolve to ``update`` on the canonical short noun:
  * ``AI算力基础设施行业`` ≡ ``AI算力基础设施产业链全景`` ≡
    ``AI算力基础设施行业-Capex驱动与国产替代下的景气周期``
  * ``光通信产业链`` ≡ ``光通信产业链-从光芯片-光模块到光纤光缆与光器件的全链条结构``
  * ``存储芯片行业`` ≡ ``半导体存储行业-AI驱动的超级周期-长协逻辑与估值重塑``
  Canonical name = short noun phrase (≤ 8 CJK chars + ``行业``/``产业链``).
- **Name format** — "name" is a short Chinese sector noun ending in
  ``行业``, ``产业链``, ``市场``, ``链`` or similar. Do NOT include subtitles
  separated by dashes, no event verbs, no quarters/years.
- Use a concise Chinese page filename in "name" when the KB language is
  Chinese. Do not use English slugs.
- The page subject must be a real industry, sector, market segment, or durable
  value-chain segment with multiple relevant companies or a persistent
  supply/demand structure.
- Do NOT create industry pages for individual companies, products,
  technologies, tickers, indexes, countries, policy events, risks, metrics,
  or one-off investment themes.
- Put reusable themes, risks, metrics, mechanisms, indicators, and monitoring
  ideas in `concepts/`, not in a dedicated investment-page directory.
- If uncertain whether a candidate is a real industry, do not include it.
- Do not duplicate company pages or ordinary reusable concepts.
- Prefer a small set of high-signal pages (≤ 3 typically). Empty arrays are fine.
- For an industry report, create at least one `industries/` page when the
  summary supports a durable sector, segment, or value-chain page.

Return ONLY valid JSON, no fences, no explanation.
"""


_CONCEPTS_PLAN_USER = """\
Based on the summary above, decide how to update the wiki's concept pages.

Existing concept pages:
{concept_briefs}

Return a JSON object with three keys:

1. "create" — new concepts not covered by any existing page. Array of objects:
   {{"name": "中文概念文件名", "title": "Human-Readable Title"}}

2. "update" — existing concepts that have significant new information from \
this document worth integrating. Array of objects:
   {{"name": "已有中文概念文件名", "title": "Existing Title"}}

3. "related" — existing concepts tangentially related to this document but \
not needing content changes, just a cross-reference link. Array of slug strings.

Rules:
- Every [[concepts/...]] link used in the summary must appear in exactly one of
  create, update, or related.
- For investment research reports, create enough durable concepts to avoid
  broken links and preserve reusable investment knowledge. Prefer 5-8
  high-signal concepts over a shallow 2-3 concept cap when the report supports it.
- Use concepts for reusable themes, risks, metrics, mechanisms, indicators,
  frameworks, policies, technologies, products, monitoring ideas, and
  bear-case/disconfirming signals.
- Do NOT create a concept that overlaps with an existing one — use "update".
- If a proposed concept is only a suffix variant of an existing concept name,
  such as added ratios, person names, or explanatory tails, use "update" on
  the shorter existing concept instead of creating a duplicate page.
- **Semantic overlap heuristic** (CRITICAL):
  before "create", scan existing concept briefs for entries that describe the
  same idea even when the wording differs. A re-named variant
  ("AIoT生态与变现" vs "AIoT生态的变现模式与增长逻辑"; "VIE_risk" vs "VIE架构风险";
  "A股并购监管窗口指导" vs "A股并购重组估值上限的监管窗口指导") is a DUPLICATE.
  Use "update" instead of "create" in these cases. **The briefs are
  intentionally written to be self-disambiguating** — read them carefully
  before claiming the proposed concept is novel.
- Do NOT create concepts that are just the document topic itself.
- Do NOT create concepts for actual companies or real industries; those belong
  in `companies/` and `industries/` only when they pass the stricter boundary.
- **Filename rule** (CRITICAL): "name" MUST be a concise Chinese phrase
  (≤ 16 字符 ideally; never exceed 30) covering the canonical concept noun.
  Do NOT include English slugs (`AIoT_monetization`), parenthetical
  explanations ("AIoT (人工智能物联网) 生态"), trailing modifiers ("…的变现模式
  与增长逻辑"), full-sentence descriptions, or quoted brand names. Pick a
  reusable concept noun, not a one-off description.
- "title" is the human-readable display form and may add a short parenthetical
  English gloss; "name" must remain the canonical Chinese noun.
- "related" is for lightweight cross-linking only, no content rewrite needed.

Return ONLY valid JSON, no fences, no explanation.
"""

_CONCEPT_PAGE_USER = """\
Write the concept page for: {title}

This concept relates to the document "{doc_name}" summarized above.
{update_instruction}

If the source is investment research, structure the page as durable investment
knowledge: definition, why it matters, source evidence, key metrics to track,
company exposure, risks/contra-evidence, and related concepts.

**Output requirements:**
- The content MUST begin with a single H1 ``# {title}`` line. No prefixes
  like ``概念：`` or ``Concept:`` — just the canonical Chinese title.
- Cite evidence with the right anchor type for the source:
  * PDF report → ``(p.12)``
  * Video/audio transcript → ``[01:23:45]`` or ``[12:34]``
  * Section-only source → ``(§核心论点)`` or ``(§3.2)``
- Use ``[[concepts/...]]`` for related reusable concepts, ``[[companies/...]]``
  for company exposure, and ``[[summaries/{doc_name}]]`` for the source.
- Do NOT write TODO placeholders. Omit claims that aren't supported by the
  provided source context.

Return a JSON object with two keys:
- "brief": A single self-disambiguating sentence (under 100 chars) defining
  this concept. The brief is what other LLM passes use to decide whether a
  future proposal duplicates this concept, so it must distinguish this idea
  from sibling concepts.
- "content": The full concept page in Markdown beginning with the H1.

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

For annual reports or financial reports, extract concrete financial and
capital-allocation facts from the source context: revenue, profit, margins,
cash flow, cash/debt, dividends, buybacks, major segment metrics, governance
or VIE notes, and material risks when available.

**Output requirements:**
- The content MUST begin with a single H1 ``# {title}`` line — exact title,
  no prefixes, no annotations.
- Cite evidence with the right anchor type for the source:
  * PDF report → ``(p.12)``
  * Video/audio transcript → ``[01:23:45]`` or ``[12:34]``
  * Section-only source → ``(§核心论点)``
- Do not write placeholders such as TODO or "numbers need to be extracted
  from the report"; omit claims that are not supported by the provided context.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing this company's
  investment relevance in this document
- "content": The full company page in Markdown beginning with the H1. Use
  ``[[concepts/...]]`` for reusable concepts and
  ``[[summaries/{doc_name}]]`` for the source summary.

Return ONLY valid JSON, no fences.
"""

_INVESTMENT_PAGE_USER = """\
Write the {page_label} page for: {title}

This {page_label} relates to the document "{doc_name}" summarized above.
{update_instruction}

Page guidance:
{page_guidance}

Keep claims traceable to the source summary. Include source evidence when
available, related companies/concepts when useful, and [[summaries/{doc_name}]].

**Output requirements:**
- The content MUST begin with a single H1 ``# {title}`` line.
- Cite evidence with the right anchor type: PDF → ``(p.12)``;
  video/audio → ``[01:23:45]``; section-only → ``(§核心论点)``.
- No TODO placeholders; omit unsupported claims.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing this page's
  investment relevance
- "content": The full {page_label} page in Markdown beginning with the H1.
  Use ``[[concepts/...]]`` for reusable concepts and
  ``[[summaries/{doc_name}]]`` for the source summary.

Return ONLY valid JSON, no fences.
"""

_CONCEPT_UPDATE_USER = """\
Update the concept page for: {title}

Current content of this page:
{existing_content}

New information from document "{doc_name}" (summarized above) should be \
captured as source-backed evidence for this concept. Use the existing content \
only as context. Focus the returned content on the new document's additions; \
OpenKB will merge it without deleting prior source evidence.

**Output requirements:**
- The content you return covers ONLY the new document's contribution
  (definition refinements, new evidence, new metrics, new exposed companies).
  Do NOT restate everything from the existing content — the merger will keep it.
- Begin with a short H2 section (no top-level H1; the canonical H1 stays in
  the existing page) such as ``## 来自《{doc_name}》的补充证据``.
- Cite evidence with the right anchor type for the source:
  * PDF report → ``(p.12)``  * Video/audio transcript → ``[01:23:45]``  * Section-only source → ``(§核心论点)``

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept — if the
  new document materially refines the canonical definition, return the
  updated brief; otherwise return the existing one unchanged.
- "content": The concept evidence from this document in Markdown (starting
  with the H2 described above).

Return ONLY valid JSON, no fences.
"""

_LONG_DOC_SUMMARY_USER = """\
This is a PageIndex summary for long document "{doc_name}" (doc_id: {doc_id}):

{content}

Based on this structured summary, write a concise overview that captures \
the key themes and findings. This will be used to generate concept pages.
If this is an annual report or financial report, preserve concrete financial
metrics, capital-allocation actions, segment details, governance notes, major
risks, and source anchors:
- PDF page anchors → ``(p.12)``
- If the source is a transcript / book chapter, use ``[01:23:45]`` or
  ``(§3.2)`` style anchors instead.
Do not write TODOs or placeholders saying that numbers still need to be
extracted. The first line of your output should be a single H1
``# {doc_name}`` (or a cleaner Chinese title derived from the document name).

Return ONLY the Markdown content (no frontmatter, no code fences).
"""

_LOCAL_LONG_DOC_SUMMARY_USER = """\
This is a page-indexed local extraction for long document "{doc_name}".

{content}

Based on this page-indexed extraction, write a high-signal summary page.
For investment research reports, preserve ratings, company names, forecasts,
valuation context, key numbers, catalysts, risks, and monitoring indicators.

**Output requirements:**
- The content MUST begin with a single H1. Prefer the document's own title;
  fall back to ``# {doc_name}`` only if no human-readable title is available.
- Cite source evidence with the right anchor:
  * PDF report → ``(p.12)`` (most common for this pipeline)
  * Video/audio transcript → ``[01:23:45]``
  * Section-only source → ``(§核心论点)``
- Do NOT write TODOs or placeholders.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown with durable ``[[concepts/...]]`` links

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
    _emit_compile_progress(f"LLM start: {step_name}")

    spinner = _Spinner(step_name)
    spinner.start()
    t0 = time.time()

    try:
        response = completion(model=model, messages=messages, **kwargs)
    except Exception as exc:
        spinner.stop("failed")
        _emit_compile_progress(f"LLM failed: {step_name}: {exc}")
        raise
    content = response.text

    usage = _format_usage(time.time() - t0, response.usage)
    spinner.stop(usage)
    _emit_compile_progress(f"LLM done: {step_name} {usage}")
    logger.debug("LLM response [%s]:\n%s", step_name, content[:500] + ("..." if len(content) > 500 else ""))
    return content.strip()


async def _llm_call_async(model: str, messages: list[dict], step_name: str) -> str:
    """Async LLM call with timing output and debug logging."""
    logger.debug("LLM request [%s]:\n%s", step_name, _fmt_messages(messages))
    _emit_compile_progress(f"LLM start: {step_name}")

    t0 = time.time()

    try:
        response = await acompletion(model=model, messages=messages)
    except Exception as exc:
        _emit_compile_progress(f"LLM failed: {step_name}: {exc}")
        raise
    content = response.text

    elapsed = time.time() - t0
    usage = _format_usage(elapsed, response.usage)
    sys.stdout.write(f"    {step_name}... {usage}\n")
    sys.stdout.flush()
    _emit_compile_progress(f"LLM done: {step_name} {usage}")
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
        or rel.startswith("industries/")
        or rel.startswith("themes/")
        or rel.startswith("metrics/")
        or rel.startswith("risks/")
        or rel.startswith("concepts/")
    )


def _managed_file_snapshot(wiki_dir: Path) -> dict[str, bytes]:
    if not wiki_dir.exists():
        return {}
    return {
        path.relative_to(wiki_dir).as_posix(): path.read_bytes()
        for path in wiki_dir.rglob("*")
        if path.is_file() and _is_compile_managed_wiki_path(path.relative_to(wiki_dir))
    }


def _sync_staged_wiki(staged_wiki: Path, wiki_dir: Path, baseline: dict[str, bytes]) -> None:
    """Copy this compile's managed wiki changes into the real wiki."""
    staged_files = {
        path.relative_to(staged_wiki)
        for path in staged_wiki.rglob("*")
        if path.is_file() and _is_compile_managed_wiki_path(path.relative_to(staged_wiki))
    }
    changed_files = {
        rel
        for rel in staged_files
        if (staged_wiki / rel).read_bytes() != baseline.get(rel.as_posix())
    }
    removed_files = {
        Path(rel)
        for rel in baseline
        if Path(rel) not in staged_files
    }

    if not wiki_dir.exists():
        wiki_dir.mkdir(parents=True, exist_ok=True)

    for rel in sorted(removed_files, reverse=True):
        path = wiki_dir / rel
        if path.exists() and path.read_bytes() == baseline.get(rel.as_posix()):
            path.unlink()

    for rel in sorted(changed_files):
        src = staged_wiki / rel
        dest = wiki_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() == src.read_bytes():
            continue
        baseline_bytes = baseline.get(rel.as_posix())
        if dest.exists() and baseline_bytes is not None and dest.read_bytes() != baseline_bytes and src.suffix == ".md":
            baseline_lines = baseline_bytes.decode("utf-8", errors="replace").splitlines()
            dest_text = dest.read_text(encoding="utf-8")
            dest_lines = dest_text.splitlines()
            staged_lines = src.read_text(encoding="utf-8").splitlines()
            additions = [line for line in staged_lines if line not in baseline_lines and line not in dest_lines]
            if additions:
                merged = dest_text.rstrip("\n") + "\n" + "\n".join(additions) + "\n"
                dest.write_text(merged, encoding="utf-8")
            continue
        shutil.copy2(src, dest)


async def _run_with_staged_wiki(kb_dir: Path, operation):
    """Run a compile operation against a staged wiki and commit on success."""
    wiki_dir = kb_dir / "wiki"
    staging_parent = kb_dir / ".openkb" / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="compile-", dir=staging_parent))
    staged_wiki = staging_root / "wiki"
    try:
        baseline = _managed_file_snapshot(wiki_dir)
        if wiki_dir.exists():
            shutil.copytree(wiki_dir, staged_wiki)
        else:
            staged_wiki.mkdir(parents=True, exist_ok=True)
        result = await operation(staged_wiki)
        _sync_staged_wiki(staged_wiki, wiki_dir, baseline)
        return result
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


def _read_investment_page_briefs(wiki_dir: Path) -> dict[str, str]:
    """Read existing dedicated investment pages by subdirectory."""
    return {
        page_type["brief_key"]: _read_page_briefs(wiki_dir, page_type["subdir"])
        for page_type in _INVESTMENT_PAGE_TYPES
    }


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
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_CONCEPT_WIKILINK_RE = re.compile(r"\[\[concepts/([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
DEFAULT_SUMMARY_LINK_FALLBACK_LIMIT = 8
_WIKI_NAMESPACE_PATH_RE = re.compile(
    r"(?<![\[\w:/.-])(?P<namespace>concepts|themes|metrics|risks|companies|industries)/"
    r"(?P<slug>[^\s\]\)>.,;:，。；：、（）()「」『』【】*]+)"
)
_MISNAMESPACED_CONCEPT_PREFIXES = (
    "themes-",
    "risks-",
    "metrics-",
    "companies-",
    "industries-",
)
_GENERATED_PAGE_PLACEHOLDER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TODO", re.compile(r"\bTODO\b", re.IGNORECASE)),
    (
        "source-evidence TODO",
        re.compile(r"add exact supporting claims", re.IGNORECASE),
    ),
    (
        "needs report extraction",
        re.compile(
            "|".join([
                r"numbers need to be extracted",
                r"key financial numbers need",
                r"\u9700\u67e5\u9605\u62a5\u8868",
                r"\u9700\u67e5\u95b1\u5831\u8868",
                r"\u9700\u4ece\u62a5\u8868\u4e2d\u63d0\u53d6",
                r"\u9700\u5f9e\u5831\u8868\u4e2d\u63d0\u53d6",
                r"\u5173\u952e\u6570\u5b57\u9700",
                r"\u95dc\u9375\u6578\u5b57\u9700",
                r"\u5177\u4f53\u6570\u503c\u9700",
                r"\u5177\u9ad4\u6578\u503c\u9700",
            ]),
            re.IGNORECASE,
        ),
    ),
)
_FINANCIAL_REPORT_DOC_HINTS = (
    "annual report",
    "financial report",
    "\u5e74\u62a5",
    "\u5e74\u5831",
    "\u5e74\u5ea6\u62a5\u544a",
    "\u5e74\u5ea6\u5831\u544a",
)
_FINANCIAL_REPORT_PAGE_KEYWORDS = (
    "revenue",
    "gross profit",
    "gross margin",
    "operating profit",
    "profit attributable",
    "non-ifrs",
    "cash flow",
    "dividend",
    "buyback",
    "share repurchase",
    "restricted cash",
    "structured entities",
    "vie",
    "consolidated income statement",
    "consolidated statement of cash flows",
    "\u6536\u5165",
    "\u6bdb\u5229",
    "\u6bdb\u5229\u7387",
    "\u7d93\u71df\u6ea2\u5229",
    "\u7ecf\u8425\u5229\u6da6",
    "\u671f\u5185\u6ea2\u5229",
    "\u671f\u5185\u5229\u6da6",
    "\u6b0a\u76ca\u6301\u6709\u4eba\u61c9\u4f54\u76c8\u5229",
    "\u6743\u76ca\u6301\u6709\u4eba\u5e94\u5360\u76c8\u5229",
    "\u73fe\u91d1\u6d41",
    "\u73b0\u91d1\u6d41",
    "\u73fe\u91d1\u53ca\u73fe\u91d1\u7b49\u50f9\u7269",
    "\u73b0\u91d1\u53ca\u73b0\u91d1\u7b49\u4ef7\u7269",
    "\u80a1\u606f",
    "\u56de\u8cfc",
    "\u56de\u8d2d",
    "\u53d7\u9650\u5236\u73fe\u91d1",
    "\u53d7\u9650\u5236\u73b0\u91d1",
    "\u67b6\u69cb\u5408\u7d04",
    "\u67b6\u6784\u5408\u7ea6",
    "\u7d50\u69cb\u6027\u5be6\u9ad4",
    "\u7ed3\u6784\u6027\u5b9e\u4f53",
    "\u6e1b\u503c",
    "\u51cf\u503c",
)
_INVESTMENT_PAGE_TYPES: tuple[dict[str, str], ...] = (
    {
        "subdir": "industries",
        "label": "industry",
        "brief_key": "industry_briefs",
        "guidance": (
            "Cover sector structure, value-chain position, capacity cycles, "
            "bottlenecks, competitive dynamics, key companies, metrics to "
            "track, catalysts, risks, and disconfirming evidence."
        ),
    },
)
_INVESTMENT_PAGE_SUBDIRS = tuple(page_type["subdir"] for page_type in _INVESTMENT_PAGE_TYPES)
_INVESTMENT_PAGE_TYPE_BY_SUBDIR = {
    page_type["subdir"]: page_type for page_type in _INVESTMENT_PAGE_TYPES
}
_LEGACY_INVESTMENT_PAGE_SUBDIRS = ("themes", "metrics", "risks")


def _sanitize_concept_name(name: str) -> str:
    """Sanitize a concept name for safe use as a filename."""
    name = unicodedata.normalize("NFKC", name)
    sanitized = _SAFE_NAME_RE.sub("-", name).strip("-")
    return sanitized or "unnamed-concept"


def _extract_chinese_from_parens(text: str) -> str:
    """Extract Chinese text from parenthetical annotations, e.g. 'AI Monetization (AI商业化变现)' -> 'AI商业化变现'."""
    for match in re.finditer(r"[（(]([^）)]+)[）)]", text):
        inner = match.group(1).strip()
        if inner and _CJK_RE.search(inner):
            return inner
    return ""


_H1_NOISE_PREFIX_RE = re.compile(
    r"^(概念[:：]|主题[:：]|Concept\s*[:：]|Topic\s*[:：])\s*",
    re.IGNORECASE,
)


def _ensure_h1(content: str, title: str) -> str:
    """Ensure generated page content has a clean H1 matching ``title``.

    Fixes three failure modes observed in the wild:
      1) LLM omits the H1 entirely → prepend ``# {title}``.
      2) LLM writes ``# 概念：foo`` / ``# Concept: foo`` noise → strip prefix.
      3) The first heading is unrelated to ``title`` (bigram Jaccard < 0.2,
         both contain CJK) → prepend a correct H1 (keep original as H2).
    """
    raw_title = str(title or "").strip()
    if not raw_title:
        return content
    # Strip frontmatter from working copy
    body = content
    fm_block = ""
    if body.startswith("---"):
        end = body.find("---", 3)
        if end != -1:
            fm_block = body[: end + 3]
            body = body[end + 3:].lstrip("\n")

    lines = body.splitlines()
    h1_idx = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            h1_idx = i
            break

    def _bigram_set(text: str) -> set[str]:
        text = unicodedata.normalize("NFKC", text or "").casefold()
        text = re.sub(r"[\s\-_/（）()【】\[\]，,。.：:、；;]+", "", text)
        if len(text) <= 1:
            return {text} if text else set()
        return {text[i:i+2] for i in range(len(text) - 1)}

    if h1_idx == -1:
        body = f"# {raw_title}\n\n" + body.lstrip("\n")
    else:
        h1_text = lines[h1_idx].strip()[2:].strip()
        cleaned = _H1_NOISE_PREFIX_RE.sub("", h1_text).strip()
        if cleaned and cleaned != h1_text:
            lines[h1_idx] = f"# {cleaned}"
            h1_text = cleaned
        # Mismatch: prepend correct H1, demote stale one to H2
        if h1_text and _CJK_RE.search(h1_text) and _CJK_RE.search(raw_title):
            a, b = _bigram_set(h1_text), _bigram_set(raw_title)
            sim = len(a & b) / len(a | b) if (a and b) else 0.0
            if sim < 0.2:
                lines[h1_idx] = f"## {h1_text}"
                lines.insert(h1_idx, "")
                lines.insert(h1_idx, f"# {raw_title}")
        body = "\n".join(lines)

    if fm_block:
        return fm_block.rstrip("\n") + "\n\n" + body
    return body


def _preferred_generated_page_name(name: str, title: str) -> str:
    """Prefer Chinese-named filenames in Chinese KBs.

    Resolution order:
      1) ``title`` 含中文 → 去掉英文括注后用 title。
      2) ``name`` 含中文 → 用 name。
      3) ``title`` / ``name`` 中括号内有中文（如 "AI Monetization (AI商业化变现)"）→ 抽出。
      4) 都无中文 → 退回 name（英文 slug 不强改，但会被审计脚本和后续 LLM 规划标记）。
    """
    raw_name = str(name or "").strip()
    raw_title = str(title or "").strip()

    if raw_title and _CJK_RE.search(raw_title):
        # 优先去掉所有英文括注，保留主中文标题
        title_name = re.sub(r"[（(][^）)]*[）)]", "", raw_title).strip()
        # 去掉明显的冒号/长破折号副标题，但保留 ASCII 连字符，避免
        # ``CPO-共封装光学`` 这类合法概念名被截断成 ``CPO``。
        title_main = re.split(r"[：:—]", title_name, 1)[0].strip() if title_name else ""
        return _sanitize_concept_name(title_main or title_name or raw_title)

    if raw_name and _CJK_RE.search(raw_name):
        return _sanitize_concept_name(raw_name)

    for source in (raw_title, raw_name):
        chinese = _extract_chinese_from_parens(source)
        if chinese:
            return _sanitize_concept_name(chinese)

    return _sanitize_concept_name(raw_name)


def _concept_alias_key(value: str) -> str:
    """Return a stable lookup key for a concept slug or title."""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\\", "/").strip()
    if value.startswith("concepts/"):
        value = value[len("concepts/"):]
    if value.endswith(".md"):
        value = value[:-3]
    return re.sub(r"[\s_\-]+", "", value).casefold()


def _wiki_path_display_label(namespace: str, slug: str) -> str:
    """Return readable text for invalid or bare wiki namespace references."""
    cleaned = slug.replace("\\", "/").strip().strip("/")
    if namespace == "concepts":
        folded = cleaned.casefold()
        for prefix in _MISNAMESPACED_CONCEPT_PREFIXES:
            if folded.startswith(prefix):
                return cleaned[len(prefix):] or cleaned
    return cleaned


def _unlink_bare_wiki_paths(text: str) -> str:
    """Render bare namespace paths as plain text unless they are valid links."""
    def replace(match: re.Match) -> str:
        namespace = match.group("namespace")
        slug = match.group("slug")
        trailing = ""
        while slug and slug[-1] in ".,;:":
            trailing = slug[-1] + trailing
            slug = slug[:-1]
        return _wiki_path_display_label(namespace, slug) + trailing

    return _WIKI_NAMESPACE_PATH_RE.sub(replace, text)


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


# Patterns that betray a "research-report-fragment" wrongly proposed as a
# company. Used by ``_looks_like_event_or_quote``; tuned against the failure
# patterns observed in legacy KBs (e.g. ``1-柴油机毛利率持续提升``,
# ``4月挖机销量有望超预期``, ``业务聚焦海外-受益于国际关系缓和``).
_COMPANY_LEADING_DIGIT_DASH_RE = re.compile(r"^\d+[\s\-—_]")
_COMPANY_EVENT_VERB_RE = re.compile(
    r"(超预期|爆发|景气|提速|提升|增长|驱动|拐点|突破|加速|演进|催化|放量|"
    r"减值|压制|滑坡|崛起|分化|严峻|改善|承压|受益|看好|获益|带动|推动|"
    r"复苏|放缓|高增|腾飞|起量|出清|抬升|下滑|出货|订单|销量|环比|同比|"
    r"利好|利空|招标|落地|启动|发布)"
)
_COMPANY_DASH_RUN_RE = re.compile(r"---+")
_COMPANY_PERCENT_RE = re.compile(r"\d{1,3}\s*%")
_COMPANY_DATAPOINT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:亿|万|千|百|倍|x|TEU|GW|GWh|MW|kWh|"
    r"元|美元|人民币|RMB|USD|EUR)|"
    r"\bQ[1-4]\b|\d{2,4}Q[1-4]\b",
    re.IGNORECASE,
)
_COMPANY_LEGAL_SUFFIX_RE = re.compile(
    r"(?:股份有限公司|有限公司|集团|控股|科技|实业|国际|"
    r"(?:Inc\.?|Corp\.?|Corporation|Limited|Ltd\.?|LLC|PLC|Holdings?|"
    r"Company|Technologies?|Industries?|Group))\s*$",
    re.IGNORECASE,
)
_KNOWN_COMPANY_NAME_RE = re.compile(
    r"^(腾讯|阿里|百度|京东|美团|拼多多|字节跳动|小米|华为|比亚迪|宁德时代|"
    r"中芯国际|中信|招商|平安|工商|建设|农业|交通|海尔|格力|美的|"
    r"茅台|五粮液|伊利|蒙牛|青岛啤酒|海天|网易|快手|微博|滴滴|"
    r"苹果|微软|英伟达|特斯拉|英特尔|AMD|台积电|三星|索尼|奈飞)"
)


def _looks_like_event_or_quote(name: str, title: str) -> str | None:
    """Return a reason if the proposed company looks like a report fragment.

    The caller should treat a non-None return value as "reject this proposal".
    Returns ``None`` when the proposal looks legitimately company-like.

    Heuristics are tuned to be **specific** (low false-positive rate) since
    the cost of rejecting a real company is high. Real companies usually
    match one of:
      * Contains a corporate-form suffix (有限公司 / 集团 / Inc / Corp …).
      * Matches the well-known company short-name allow-list.
      * Short noun phrase (≤ 12 CJK chars, no event/verb keywords).
    Anything else that *also* shows event-fragment signals is rejected.
    """
    name = str(name or "").strip()
    title = str(title or "").strip()
    payload = title or name
    if not payload:
        return "empty name"

    # Strip parenthetical English/Chinese gloss for analysis, e.g.
    # "中天科技 (ZTT)" -> "中天科技".
    base = re.sub(r"[（(][^）)]*[）)]", "", payload).strip()
    if not base:
        base = payload

    # Allow-list short-circuit: corporate-form suffix or famous name.
    if _COMPANY_LEGAL_SUFFIX_RE.search(base) or _KNOWN_COMPANY_NAME_RE.match(base):
        return None

    # 1. Numeric/dash prefix is a very strong report-fragment signal.
    if _COMPANY_LEADING_DIGIT_DASH_RE.match(base):
        return "starts with digit+dash (looks like a report bullet)"

    # 2. Long run of dashes is typical of LLM-merged section titles.
    if _COMPANY_DASH_RUN_RE.search(base):
        return "contains '---' run (looks like a fragment header)"

    # 3. Length: real Chinese company short-names are rarely > 12 CJK chars.
    cjk_count = sum(1 for c in base if "㐀" <= c <= "鿿")
    if cjk_count >= 16:
        return f"too long ({cjk_count} CJK chars; companies are usually ≤ 12)"

    # 4. Event/finance verbs combined with non-trivial length = quote, not name.
    if _COMPANY_EVENT_VERB_RE.search(base) and cjk_count >= 6:
        return "contains event/finance verbs (looks like a quote, not a name)"

    # 5. Embedded percentages or "N年" patterns are data points, not names.
    if _COMPANY_PERCENT_RE.search(base):
        return "contains percentage figure (looks like a data point)"
    if _COMPANY_DATAPOINT_RE.search(base):
        return "contains numeric + unit / quarter ref (looks like a data point)"
    if re.search(r"\d{2,4}\s*年", base) and cjk_count >= 4:
        return "contains year reference (looks like a forecast statement)"

    return None


def _canonicalize_company_item(item: dict) -> dict | None:
    """Normalize company plan items to safe filenames; reject report fragments.

    Returns ``None`` when the proposal triggers ``_looks_like_event_or_quote``
    so research-report bullets and quote fragments don't slip into companies/.
    """
    name = str(item.get("name", "")).strip()
    if not name:
        return None
    title = str(item.get("title", name)).strip() or name
    rejection = _looks_like_event_or_quote(name, title)
    if rejection is not None:
        logger.info("Rejected company proposal %r: %s", title or name, rejection)
        return None
    action = str(item.get("action", "create")).strip().lower()
    if action not in {"create", "update"}:
        action = "create"
    return {
        "name": _preferred_generated_page_name(name, title),
        "title": title,
        "action": action,
    }


def _canonicalize_investment_page_item(item: dict) -> dict | None:
    """Normalize dedicated investment page plan items to safe slugs."""
    name = str(item.get("name", "")).strip()
    if not name:
        return None
    title = str(item.get("title", name)).strip() or name
    action = str(item.get("action", "create")).strip().lower()
    if action not in {"create", "update"}:
        action = "create"
    return {
        "name": _preferred_generated_page_name(name, title),
        "title": title,
        "action": action,
    }


def _resolve_company_items_against_registry(
    items: list[dict],
    registry: EntityRegistry | None,
) -> tuple[list[dict], list[ResolvedEntity]]:
    """Resolve company plan items to registered canonical company ids.

    Unknown companies intentionally keep the existing LLM-proposed item so
    unregistered but useful company pages can still be created automatically.
    """
    if registry is None:
        return items, []

    resolved_items: list[dict] = []
    resolved_entities: list[ResolvedEntity] = []
    seen: set[str] = set()
    for item in items:
        surface = str(item.get("title") or item.get("name") or "").strip()
        match = registry.resolve(surface, namespace_hint="company")
        if match is None:
            slug = _sanitize_concept_name(str(item.get("name") or ""))
            if slug and slug not in seen:
                seen.add(slug)
                resolved_items.append(item)
            continue

        if match.canonical_id in seen:
            continue
        seen.add(match.canonical_id)
        resolved_entities.append(match)
        resolved_items.append({
            "name": match.canonical_id,
            "title": match.canonical_name,
            "action": "update",
        })
    return resolved_items, resolved_entities


def _empty_investment_page_plan() -> dict[str, list[dict]]:
    """Return an empty dedicated investment page plan keyed by subdir."""
    return {subdir: [] for subdir in _INVESTMENT_PAGE_SUBDIRS}


def _parse_investment_page_plan(parsed: list | dict) -> dict[str, list[dict]]:
    """Extract dedicated industry page arrays from an LLM plan."""
    plan = _empty_investment_page_plan()
    if not isinstance(parsed, dict):
        return plan

    for subdir in _INVESTMENT_PAGE_SUBDIRS:
        raw_items = parsed.get(subdir, [])
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            canonical = _canonicalize_investment_page_item(item)
            if canonical is not None:
                plan[subdir].append(canonical)
    return plan


def _dedupe_investment_page_plan(
    wiki_dir: Path, plan: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Apply fuzzy dedupe to industries / themes / metrics / risks plan items.

    For each subdir, build that namespace's signatures and resolve every
    proposed item against existing pages. If a fuzzy match exists we coerce
    ``action`` to ``update`` and adopt the existing slug; otherwise we keep
    the proposal as-is. This is the same logic ``_dedupe_concept_plan``
    applies to ``concepts/``, factored out so all generated namespaces share
    the safety net.
    """
    deduped: dict[str, list[dict]] = _empty_investment_page_plan()
    for subdir, items in plan.items():
        if not items:
            continue
        signatures = _existing_namespace_signatures(wiki_dir, subdir)
        # Aliases: slug + first H1 of each existing page, keyed by alias-key.
        aliases: dict[str, str] = {}
        existing_titles: dict[str, str] = {}
        pages_dir = wiki_dir / subdir
        if pages_dir.exists():
            for path in sorted(pages_dir.glob("*.md")):
                slug = path.stem
                title = _concept_page_title(path)
                existing_titles[slug] = title
                for value in (slug, title):
                    if value:
                        aliases[_concept_alias_key(value)] = slug

        seen_slugs: set[str] = set()
        for item in items:
            proposed_name = str(item.get("name") or "")
            proposed_title = str(item.get("title") or proposed_name)
            requested_action = str(item.get("action", "create")).strip().lower()
            resolved_slug = _resolve_duplicate_concept_slug(
                proposed_name, proposed_title, aliases,
                signatures=signatures, brief="",
            )
            if resolved_slug is not None:
                slug = resolved_slug
                title = existing_titles.get(slug) or proposed_title
                action = "update"
            else:
                slug = _sanitize_concept_name(proposed_name)
                title = proposed_title
                action = requested_action if requested_action in {"create", "update"} else "create"
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            deduped[subdir].append({"name": slug, "title": title, "action": action})
    return deduped


def _resolve_investment_page_plan_against_registry(
    plan: dict[str, list[dict]],
    registry: EntityRegistry | None,
) -> tuple[dict[str, list[dict]], list[ResolvedEntity]]:
    """Resolve industry plan items to registered canonical industry ids.

    Unknown industries keep the existing behavior and can still be generated.
    Registered company aliases are dropped from industry plans to avoid pages
    like ``industries/腾讯控股.md``.
    """
    if registry is None:
        return plan, []

    resolved_plan = _empty_investment_page_plan()
    resolved_entities: list[ResolvedEntity] = []
    for subdir, items in plan.items():
        seen: set[str] = set()
        for item in items:
            surface = str(item.get("title") or item.get("name") or "").strip()
            if registry.resolve(surface, namespace_hint="company") is not None:
                continue

            match = registry.resolve(surface, namespace_hint="industry")
            if match is None:
                slug = _sanitize_concept_name(str(item.get("name") or ""))
                if slug and slug not in seen:
                    seen.add(slug)
                    resolved_plan[subdir].append(item)
                continue

            if match.canonical_id in seen:
                continue
            seen.add(match.canonical_id)
            resolved_entities.append(match)
            resolved_plan[subdir].append({
                "name": match.canonical_id,
                "title": match.canonical_name,
                "action": "update",
            })
    return resolved_plan, resolved_entities


def _planned_investment_page_slugs(plan: dict[str, list[dict]]) -> dict[str, set[str]]:
    """Return planned dedicated investment page slugs by subdir."""
    return {
        subdir: {
            _sanitize_concept_name(str(item["name"]))
            for item in items
            if isinstance(item, dict) and item.get("name")
        }
        for subdir, items in plan.items()
    }


def _canonicalize_concept_item(item: dict) -> dict | None:
    """Normalize concept plan items to canonical durable slugs."""
    name = str(item.get("name", "")).strip()
    if not name:
        return None
    title = str(item.get("title", name)).strip() or name
    alias = _CONCEPT_PLAN_NAME_ALIASES.get(_concept_alias_key(name))
    if alias is not None:
        name, title = alias
    return {"name": _preferred_generated_page_name(name, title), "title": title}


def _concept_prefix(value: str) -> str:
    """Return a shorter base concept name when a suffix variant is obvious."""
    for separator in ("--", "\u2014", "\uff1a", ":", "\uff0d", "-"):
        if separator not in value:
            continue
        prefix = value.split(separator, 1)[0].strip()
        if len(prefix) >= 2:
            return prefix
    return ""


def _concept_page_title(path: Path) -> str:
    """Read the first H1 from a concept page, falling back to its stem."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path.stem
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].lstrip("\n")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def _existing_concept_aliases(wiki_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return alias keys and display titles for existing concept pages."""
    aliases: dict[str, str] = {}
    titles: dict[str, str] = {}
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return aliases, titles

    for path in sorted(concepts_dir.glob("*.md")):
        slug = path.stem
        title = _concept_page_title(path)
        titles[slug] = title
        for value in (slug, title):
            if value:
                aliases[_concept_alias_key(value)] = slug
    return aliases, titles


def _concept_unigrams(text: str) -> set[str]:
    """Character unigram set covering both CJK and alphanumerics (case-folded, NFKC).

    Used as a cheap, dependency-free semantic signature for fuzzy duplicate
    detection across concept name/title/brief.
    """
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return {
        c for c in text
        if c.isalnum() or "㐀" <= c <= "鿿"
    }


def _existing_namespace_signatures(
    wiki_dir: Path, subdir: str,
) -> dict[str, tuple[set[str], set[str]]]:
    """Generalised version of ``_existing_concept_signatures`` for any subdir.

    Same dual-signature shape — tight (slug+title) + wide (slug+title+brief).
    Used by industries / themes / metrics / risks dedupe paths so they get
    the same Jaccard-based variant detection as concepts.
    """
    sigs: dict[str, tuple[set[str], set[str]]] = {}
    pages_dir = wiki_dir / subdir
    if not pages_dir.exists():
        return sigs
    for path in sorted(pages_dir.glob("*.md")):
        slug = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        brief = ""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                for line in text[:end].split("\n"):
                    if line.startswith("brief:"):
                        brief = line[len("brief:"):].strip()
                        break
        title = _concept_page_title(path)
        tight = _concept_unigrams(f"{slug} {title}")
        wide = tight | _concept_unigrams(brief)
        sigs[slug] = (tight, wide)
    return sigs


def _existing_concept_signatures(wiki_dir: Path) -> dict[str, tuple[set[str], set[str]]]:
    """Backwards-compatible alias for concepts namespace."""
    return _existing_namespace_signatures(wiki_dir, "concepts")


def _similar_concept_slug(
    name: str,
    title: str,
    brief: str,
    signatures: dict[str, tuple[set[str], set[str]]],
    tight_threshold: float = 0.50,
    wide_threshold: float = 0.40,
    overlap_threshold: float = 0.70,
    min_overlap: int = 3,
    overlap_min: int = 5,
) -> str | None:
    """Return the best existing slug matching either signature, or None.

    Three-pass strategy:
      * Tight Jaccard: target(name+title) vs candidate(name+title). Captures
        Chinese-variant concept names.
      * Wide Jaccard: target(name+title+brief) vs candidate(name+title+brief).
        Captures cross-language variants where briefs supply CJK overlap.
      * Overlap coefficient: ``inter / min(|target|, |candidate|)``. Captures
        cases where one side has a long descriptive name/brief that dilutes
        Jaccard. Requires ``overlap_min`` raw character overlap to avoid
        trivial short-string false positives.
    """
    target_tight = _concept_unigrams(f"{name} {title or ''}")
    target_wide = target_tight | _concept_unigrams(brief or "")
    if len(target_tight) < min_overlap and len(target_wide) < min_overlap:
        return None

    best_slug: str | None = None
    best_score = 0.0

    for slug, (cand_tight, cand_wide) in signatures.items():
        score = 0.0
        if len(cand_tight) >= min_overlap and len(target_tight) >= min_overlap:
            inter = len(target_tight & cand_tight)
            if inter >= min_overlap:
                union = len(target_tight | cand_tight)
                sim = inter / union if union else 0.0
                if sim >= tight_threshold:
                    score = max(score, sim)
        if score == 0 and len(cand_wide) >= min_overlap and len(target_wide) >= min_overlap:
            inter = len(target_wide & cand_wide)
            if inter >= min_overlap:
                union = len(target_wide | cand_wide)
                sim = inter / union if union else 0.0
                if sim >= wide_threshold:
                    score = max(score, sim)
        if score == 0:
            # Overlap-coefficient fallback for long-name dilution cases.
            inter_t = len(target_tight & cand_tight)
            if inter_t >= overlap_min:
                base = min(len(target_tight), len(cand_tight))
                if base:
                    sim = inter_t / base
                    if sim >= overlap_threshold:
                        score = max(score, sim * 0.95)  # slight penalty vs Jaccard
        if score > best_score:
            best_score, best_slug = score, slug

    return best_slug


def _resolve_duplicate_concept_slug(
    name: str,
    title: str,
    aliases: dict[str, str],
    signatures: dict[str, tuple[set[str], set[str]]] | None = None,
    brief: str = "",
) -> str | None:
    """Resolve a concept proposal to an existing canonical slug when obvious."""
    candidates = [
        str(name or "").strip(),
        str(title or "").strip(),
        _sanitize_concept_name(str(name or "")),
        _sanitize_concept_name(str(title or "")),
    ]
    for value in candidates:
        if not value:
            continue
        key = _concept_alias_key(value)
        if key in aliases:
            return aliases[key]
        prefix = _concept_prefix(value)
        if prefix:
            prefix_key = _concept_alias_key(prefix)
            if prefix_key in aliases:
                return aliases[prefix_key]
    # Last-resort fuzzy match is valuable for CJK / cross-language concept
    # variants, but too aggressive for English-only names like
    # ``flash-attention`` vs ``attention``.
    if signatures and any(_CJK_RE.search(value or "") for value in (name, title, brief)):
        hit = _similar_concept_slug(name, title, brief, signatures)
        if hit:
            return hit
    return None


def _dedupe_concept_plan(wiki_dir: Path, plan: dict) -> dict:
    """Collapse obvious duplicate concept variants into one canonical plan."""
    existing_aliases, existing_titles = _existing_concept_aliases(wiki_dir)
    existing_signatures: dict[str, tuple[set[str], set[str]]] = _existing_concept_signatures(wiki_dir)
    aliases = dict(existing_aliases)
    deduped = {"create": [], "update": [], "related": []}
    chosen_actions: dict[str, str] = {}

    def register_aliases(slug: str, *values: str) -> None:
        for value in values:
            if not value:
                continue
            aliases[_concept_alias_key(value)] = slug

    def upsert_page(action: str, slug: str, title: str) -> None:
        current = chosen_actions.get(slug)
        item = {"name": slug, "title": title}
        if current == "update":
            return
        if current == "create" and action == "update":
            deduped["create"] = [entry for entry in deduped["create"] if entry["name"] != slug]
            deduped["update"].append(item)
            chosen_actions[slug] = "update"
            return
        if current == "related" and action in {"create", "update"}:
            deduped["related"] = [entry for entry in deduped["related"] if entry != slug]
        if current is None or current == "related":
            deduped[action].append(item)
            chosen_actions[slug] = action

    for action in ("create", "update"):
        canonical_items = [
            canonical for item in plan.get(action, [])
            if isinstance(item, dict)
            for canonical in [_canonicalize_concept_item(item)]
            if canonical is not None
        ]
        canonical_items.sort(
            key=lambda item: (
                len(_sanitize_concept_name(str(item["name"]))),
                len(str(item.get("title") or "")),
                _concept_alias_key(str(item["name"])),
            )
        )
        for item in canonical_items:
            proposed_name = str(item["name"])
            proposed_title = str(item.get("title") or proposed_name)
            proposed_brief = str(item.get("brief") or "")
            slug = _resolve_duplicate_concept_slug(
                proposed_name, proposed_title, aliases,
                signatures=existing_signatures, brief=proposed_brief,
            )
            if slug is None:
                slug = _sanitize_concept_name(proposed_name)
            resolved_title = existing_titles.get(slug) or proposed_title
            resolved_action = "update" if slug in existing_titles or slug != _sanitize_concept_name(proposed_name) else action
            upsert_page(resolved_action, slug, resolved_title)
            register_aliases(slug, proposed_name, proposed_title, slug, _concept_prefix(proposed_name), _concept_prefix(proposed_title))

    for related in plan.get("related", []):
        raw = str(related).strip()
        if not raw:
            continue
        slug = _resolve_duplicate_concept_slug(
            raw, raw, aliases, signatures=existing_signatures,
        ) or _sanitize_concept_name(raw)
        if not slug or chosen_actions.get(slug) in {"create", "update"}:
            continue
        if slug not in deduped["related"]:
            deduped["related"].append(slug)
            chosen_actions[slug] = "related"
        register_aliases(slug, raw, slug, _concept_prefix(raw))

    return deduped


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


def _registered_entity_alias_keys(registry: EntityRegistry | None, entity_type: str) -> set[str]:
    """Return alias keys for registered company or industry records."""
    if registry is None:
        return set()
    keys: set[str] = set()
    for record in registry.records:
        if record.entity_type != entity_type:
            continue
        for value in record.all_names():
            keys.add(_concept_alias_key(value))
    return keys


def _registered_entity_link_aliases(registry: EntityRegistry | None) -> dict[str, str]:
    """Return wikilink alias keys for registered company and industry pages."""
    if registry is None:
        return {}
    aliases: dict[str, str] = {}
    for record in registry.records:
        namespace = "companies" if record.entity_type == "company" else "industries"
        for value in record.all_names():
            aliases[_concept_alias_key(f"{namespace}/{value}")] = record.path
    return aliases


def _filter_concept_plan_against_registered_industries(
    plan: dict,
    registry: EntityRegistry | None,
) -> dict:
    """Remove registered industry names from concept plan.

    Company aliases are already included in ``company_keys`` by the caller.
    This keeps the scope narrow: concepts are not otherwise changed.
    """
    industry_keys = _registered_entity_alias_keys(registry, "industry")
    if not industry_keys:
        return plan

    filtered = {"create": [], "update": [], "related": []}
    for action in ("create", "update"):
        for item in plan.get(action, []):
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name", "")).strip()
            raw_title = str(item.get("title", raw_name)).strip()
            if _concept_alias_key(raw_name) in industry_keys or _concept_alias_key(raw_title) in industry_keys:
                continue
            filtered[action].append(item)

    for related in plan.get("related", []):
        raw = str(related).strip()
        if raw and _concept_alias_key(raw) not in industry_keys:
            filtered["related"].append(raw)

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
    aliases, _existing_titles = _existing_concept_aliases(wiki_dir)

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
        if slug and "/" in slug and slug in valid_pages:
            if match.group(2):
                return f"[[{slug}|{label}]]"
            return f"[[{slug}]]"
        if slug in allowed_slugs:
            return f"[[concepts/{slug}]]"

        if raw_target.startswith("concepts/"):
            concept_target = raw_target[len("concepts/"):]
            candidate = _sanitize_concept_name(concept_target)
            if candidate in allowed_slugs:
                return f"[[concepts/{candidate}]]"
            if match.group(2):
                return label
            return _wiki_path_display_label("concepts", concept_target)

        if raw_target in valid_pages:
            if match.group(2):
                return f"[[{raw_target}|{label}]]"
            return f"[[{raw_target}]]"

        return label

    return _unlink_bare_wiki_paths(_WIKILINK_RE.sub(replace, text))


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


def _write_concept(wiki_dir: Path, name: str, content: str, source_file: str, is_update: bool, brief: str = "") -> str | None:
    """Write or update a concept page, managing the sources frontmatter."""
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_concept_name(name)
    path = (concepts_dir / f"{safe_name}.md").resolve()
    if not path.is_relative_to(concepts_dir.resolve()):
        logger.warning("Concept name escapes concepts dir: %s", name)
        return None

    if is_update and path.exists():
        existing = path.read_text(encoding="utf-8")
        existing_sources = _frontmatter_source_entries(existing)
        if not existing_sources:
            existing_sources = [source_file] if source_file in existing else []
        sources = list(existing_sources)
        if source_file not in sources:
            sources.append(source_file)
        clean = content
        if clean.startswith("---"):
            end = clean.find("---", 3)
            if end != -1:
                clean = clean[end + 3:].lstrip("\n")
        clean = _ensure_source_evidence_section(clean, source_file)
        multi_source_update = bool(existing_sources) and (len(sources) > 1 or len(existing_sources) > 1)
        if multi_source_update:
            body = _merge_multisource_generated_content(existing, existing_sources, source_file, clean)
            update_brief = bool(brief)
            existing = _set_frontmatter_sources_and_brief(
                existing,
                sources,
                brief,
                update_brief=update_brief,
            )
            if existing.startswith("---"):
                end = existing.find("---", 3)
                if end != -1:
                    existing = existing[:end + 3] + "\n\n" + body
                else:
                    existing = body
            else:
                existing = body
        else:
            existing = _set_frontmatter_sources_and_brief(
                existing,
                sources or [source_file],
                brief,
                update_brief=bool(brief),
            )
            if existing.startswith("---"):
                end = existing.find("---", 3)
                if end != -1:
                    existing = existing[:end + 3] + "\n\n" + clean
                else:
                    existing = clean
            else:
                existing = clean
        path.write_text(existing, encoding="utf-8")
        if brief and (not multi_source_update or update_brief):
            return brief
        return None
    else:
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].lstrip("\n")
        content = _ensure_source_evidence_section(content, source_file)
        fm_lines = [f"sources: [{source_file}]"]
        if brief:
            fm_lines.append(f"brief: {brief}")
        frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
        path.write_text(frontmatter + content, encoding="utf-8")
        return brief or None


def _source_link_for_file(source_file: str) -> str:
    return source_file[:-3] if source_file.endswith(".md") else source_file


def _extract_frontmatter_brief(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("---", 3)
    if end == -1:
        return ""
    frontmatter = text[:end + 3]
    match = re.search(r"^brief:\s*(.*?)\s*$", frontmatter, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _set_frontmatter_sources_and_brief(
    text: str,
    sources: list[str],
    brief: str = "",
    *,
    update_brief: bool = False,
) -> str:
    if not text.startswith("---"):
        fm_lines = [f"sources: [{', '.join(sources)}]"]
        if brief and update_brief:
            fm_lines.append(f"brief: {brief}")
        return "---\n" + "\n".join(fm_lines) + "\n---\n\n" + text.lstrip()

    end = text.find("---", 3)
    if end == -1:
        fm_lines = [f"sources: [{', '.join(sources)}]"]
        if brief and update_brief:
            fm_lines.append(f"brief: {brief}")
        return "---\n" + "\n".join(fm_lines) + "\n---\n\n" + text

    fm = text[:end + 3]
    body = text[end + 3:]
    source_line = f"sources: [{', '.join(sources)}]"
    if re.search(r"^sources:\s*\[.*?\]\s*$", fm, re.MULTILINE):
        fm = re.sub(r"^sources:\s*\[.*?\]\s*$", source_line, fm, count=1, flags=re.MULTILINE)
    else:
        fm = fm.replace("---\n", f"---\n{source_line}\n", 1)
    if update_brief and brief:
        if re.search(r"^brief:.*$", fm, re.MULTILINE):
            fm = re.sub(r"^brief:.*$", f"brief: {brief}", fm, count=1, flags=re.MULTILINE)
        else:
            fm = fm.replace("---\n", f"---\nbrief: {brief}\n", 1)
    return fm + body


def _extract_year(value: str) -> int | None:
    match = re.search(r"(20\d{2})", value)
    return int(match.group(1)) if match else None


def _should_update_brief_for_source(existing_sources: list[str], source_file: str, existing_text: str) -> bool:
    if not _extract_frontmatter_brief(existing_text):
        return True
    new_year = _extract_year(source_file)
    old_years = [year for source in existing_sources if (year := _extract_year(source)) is not None]
    if new_year is None or not old_years:
        return True
    return new_year >= max(old_years)


def _strip_leading_h1(content: str) -> str:
    lines = content.lstrip().splitlines()
    if lines and re.match(r"^#(?!#)\s+", lines[0]):
        return "\n".join(lines[1:]).lstrip()
    return content.strip()


def _extract_leading_h1(content: str) -> str:
    lines = content.lstrip().splitlines()
    if lines and re.match(r"^#(?!#)\s+", lines[0]):
        return lines[0].strip()
    return ""


def _source_update_section(source_file: str, content: str) -> str:
    source_link = _source_link_for_file(source_file)
    body = _strip_leading_h1(content).strip()
    if not body:
        body = f"- [[{source_link}]]"
    return f"## Source Update: [[{source_link}]]\n\n{body.rstrip()}\n"


def _has_source_update_sections(content: str) -> bool:
    return bool(re.search(r"^## Source Update: \[\[[^\]]+\]\]\s*$", content, re.MULTILINE))


def _replace_or_append_source_update_section(content: str, source_file: str, section: str) -> str:
    source_link = re.escape(_source_link_for_file(source_file))
    pattern = re.compile(
        rf"^## Source Update: \[\[{source_link}\]\]\s*\n.*?(?=^## Source Update: \[\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    replacement = section.rstrip() + "\n\n"
    if pattern.search(content):
        return pattern.sub(replacement, content).rstrip() + "\n"
    return content.rstrip() + "\n\n" + section.rstrip() + "\n"


def _merge_multisource_generated_content(
    existing_text: str,
    existing_sources: list[str],
    source_file: str,
    clean_content: str,
) -> str:
    if existing_text.startswith("---"):
        end = existing_text.find("---", 3)
        existing_body = existing_text[end + 3:].lstrip("\r\n") if end != -1 else existing_text
    else:
        existing_body = existing_text

    title = _extract_leading_h1(existing_body) or _extract_leading_h1(clean_content)
    body = _strip_leading_h1(existing_body)
    if not _has_source_update_sections(body) and len(existing_sources) == 1:
        body = _source_update_section(existing_sources[0], body)
    body = _replace_or_append_source_update_section(
        body,
        source_file,
        _source_update_section(source_file, clean_content),
    )
    if title:
        return title + "\n\n" + body.rstrip() + "\n"
    return body.rstrip() + "\n"


def _write_company(wiki_dir: Path, name: str, content: str, source_file: str, is_update: bool, brief: str = "") -> str | None:
    """Write or update a company page, managing the sources frontmatter."""
    companies_dir = wiki_dir / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_concept_name(name)
    path = (companies_dir / f"{safe_name}.md").resolve()
    if not path.is_relative_to(companies_dir.resolve()):
        logger.warning("Company name escapes companies dir: %s", name)
        return None

    if is_update and path.exists():
        existing = path.read_text(encoding="utf-8")
        existing_sources = _frontmatter_source_entries(existing)
        if not existing_sources:
            existing_sources = [source_file] if source_file in existing else []
        sources = list(existing_sources)
        if source_file not in sources:
            sources.append(source_file)
        clean = content
        if clean.startswith("---"):
            end = clean.find("---", 3)
            if end != -1:
                clean = clean[end + 3:].lstrip("\n")
        clean = _ensure_source_evidence_section(clean, source_file)
        multi_source_update = bool(existing_sources) and (len(sources) > 1 or len(existing_sources) > 1)
        if multi_source_update:
            body = _merge_multisource_generated_content(existing, existing_sources, source_file, clean)
            update_brief = bool(brief) and _should_update_brief_for_source(existing_sources, source_file, existing)
            existing = _set_frontmatter_sources_and_brief(
                existing,
                sources,
                brief,
                update_brief=update_brief,
            )
            if existing.startswith("---"):
                end = existing.find("---", 3)
                if end != -1:
                    existing = existing[:end + 3] + "\n\n" + body
                else:
                    existing = body
            else:
                existing = body
        else:
            existing = _set_frontmatter_sources_and_brief(
                existing,
                sources or [source_file],
                brief,
                update_brief=bool(brief),
            )
            if existing.startswith("---"):
                end = existing.find("---", 3)
                if end != -1:
                    existing = existing[:end + 3] + "\n\n" + clean
                else:
                    existing = clean
            else:
                existing = clean
        path.write_text(existing, encoding="utf-8")
        if brief and (not multi_source_update or update_brief):
            return brief
        return None
    else:
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].lstrip("\n")
        content = _ensure_source_evidence_section(content, source_file)
        fm_lines = [f"sources: [{source_file}]"]
        if brief:
            fm_lines.append(f"brief: {brief}")
        frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
        path.write_text(frontmatter + content, encoding="utf-8")
        return brief or None


def _write_investment_page(
    wiki_dir: Path,
    subdir: str,
    name: str,
    content: str,
    source_file: str,
    is_update: bool,
    brief: str = "",
) -> None:
    """Write or update a dedicated investment page with managed frontmatter."""
    if subdir not in _INVESTMENT_PAGE_SUBDIRS:
        logger.warning("Unsupported investment page subdir: %s", subdir)
        return

    pages_dir = wiki_dir / subdir
    pages_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_concept_name(name)
    path = (pages_dir / f"{safe_name}.md").resolve()
    if not path.is_relative_to(pages_dir.resolve()):
        logger.warning("Investment page name escapes %s dir: %s", subdir, name)
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
        clean = _ensure_source_evidence_section(clean, source_file)
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
        content = _ensure_source_evidence_section(content, source_file)
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


# Evidence anchor patterns. ``_GENERATED_PAGE_REF_RE`` is kept for backwards
# compatibility (callers that only want PDF-style page refs). The richer
# ``_EVIDENCE_ANCHOR_PATTERNS`` list drives the multi-modal extractor below:
# PDF page, video/audio timestamp, chapter/section marker.
_GENERATED_PAGE_REF_RE = re.compile(r"\b(?:p\.?\s*|page\s+)(\d{1,4})\b", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"\[?\b(\d{1,2}:[0-5]\d(?::[0-5]\d)?)\b\]?")
_CHAPTER_RE = re.compile(
    r"(?:§|第\s*)(\d+(?:\.\d+)?)(?:\s*章|\s*节)?|"
    r"\b(?:ch\.|chapter|chap\.)\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

_EVIDENCE_ANCHOR_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # (kind, regex, format-template). format-template gets ``{0}`` substituted
    # with the captured anchor (first non-None group). The kind label is what
    # gets stored in the evidence ``page`` field as a human-readable anchor.
    ("page", _GENERATED_PAGE_REF_RE, "p.{0}"),
    ("timestamp", _TIMESTAMP_RE, "{0}"),
    ("chapter", _CHAPTER_RE, "§{0}"),
)


def _evidence_anchor_value(kind: str, formatted_anchor: str) -> str:
    """Return the stored evidence-map value for a detected anchor."""
    if kind == "page":
        match = re.search(r"(\d{1,4})", formatted_anchor)
        if match:
            return match.group(1)
    return formatted_anchor


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
    snippet = _TIMESTAMP_RE.sub("", snippet, count=1)
    snippet = snippet.lstrip(" :-")
    return snippet[:limit].rstrip()


def _detect_evidence_anchor(line: str) -> tuple[str, str] | None:
    """Detect the best evidence anchor in ``line``.

    Returns ``(anchor_kind, formatted_anchor)`` or ``None``. Patterns are
    tried in priority order: PDF page > timestamp > chapter. The first match
    wins so PDF-rich summaries keep their familiar ``p.N`` output.
    """
    for kind, regex, template in _EVIDENCE_ANCHOR_PATTERNS:
        match = regex.search(line)
        if not match:
            continue
        # Use the first non-empty capturing group so multi-branch patterns
        # (e.g. the chapter regex) still produce a clean anchor.
        for group in match.groups():
            if group:
                return kind, template.format(group)
    return None


def _section_anchors_from_summary(content: str, max_items: int = 12) -> list[dict[str, str]]:
    """Fallback evidence for transcripts: emit ``section: <H2>`` anchors.

    Used when a summary lacks any page/timestamp marker — common for mp4 or
    audio transcripts where the upstream pipeline didn't preserve timing.
    Each non-trivial H2 becomes one evidence row pointing back to the
    same source, so the query agent at least has a section-level handle.
    """
    body = _strip_frontmatter_block(content)
    anchors: list[dict[str, str]] = []
    current_h2: str | None = None
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            current_h2 = stripped[3:].strip().lstrip("#").strip()
            continue
        if not current_h2:
            continue
        line = " ".join(stripped.split())
        if not line or stripped.startswith("#"):
            continue
        snippet = _clean_evidence_snippet(line)
        if not snippet or len(snippet) < 20:
            continue
        anchors.append({
            "anchor": f"§{current_h2}",
            "snippet": snippet,
        })
        if len(anchors) >= max_items:
            break
    return anchors


def _extract_page_reference_evidence(
    content: str,
    source_file: str,
    source_link: str,
    *,
    max_items: int = 12,
) -> list[dict[str, str]]:
    """Extract multi-modal evidence anchors from generated Markdown content.

    Recognises PDF page refs (``p.N``), video/audio timestamps
    (``[01:23:45]`` / ``12:34``), and chapter markers (``§3.2`` / ``Ch.5``
    / ``第3章``). If none of those exist (typical for transcript summaries),
    falls back to section anchors derived from the document's H2 headings.
    """
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in _strip_frontmatter_block(content).splitlines():
        line = " ".join(raw_line.strip().split())
        if not line or line.startswith("#"):
            continue
        anchor = _detect_evidence_anchor(line)
        if anchor is None:
            continue
        kind, formatted_anchor = anchor
        page = _evidence_anchor_value(kind, formatted_anchor)
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

    if evidence:
        return evidence

    # Transcript fallback: section-level anchors so evidence_map.json still
    # gets populated for mp4/audio summaries that have no page markers.
    for item in _section_anchors_from_summary(content, max_items=max_items):
        key = (item["anchor"], item["snippet"].casefold())
        if key in seen:
            continue
        seen.add(key)
        evidence.append({
            "source": source_file,
            "link": source_link,
            "page": item["anchor"],
            "snippet": item["snippet"],
        })
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


def _generated_page_quality_issues(
    content: str,
    source_file: str,
    *,
    require_page_evidence: bool = False,
) -> list[str]:
    """Return quality issues that should prevent writing generated pages."""
    issues: list[str] = []
    for label, pattern in _GENERATED_PAGE_PLACEHOLDER_PATTERNS:
        if pattern.search(content):
            issues.append(f"placeholder generated content ({label})")
            break
    if require_page_evidence and not _extract_generated_page_evidence(content, source_file):
        issues.append("missing page evidence")
    return issues


def _clean_existing_generated_page_artifacts(text: str) -> str:
    """Remove stale generated placeholders and bare namespace paths from old pages."""
    cleaned = _unlink_bare_wiki_paths(text)
    lines = [
        line
        for line in cleaned.splitlines()
        if not any(pattern.search(line) for _, pattern in _GENERATED_PAGE_PLACEHOLDER_PATTERNS)
    ]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()
    return cleaned + "\n"


def _ensure_source_evidence_section(content: str, source_file: str) -> str:
    """Ensure generated pages always include a Source Evidence section."""
    if re.search(r"^## Source Evidence\s*$", content, re.MULTILINE):
        return content

    source_link = source_file[:-3] if source_file.endswith(".md") else source_file
    evidence = _extract_generated_page_evidence(content, source_file)
    if evidence:
        lines = [
            f"- [[{item['link']}]] p.{item['page']}: {item['snippet']}"
            for item in evidence
        ]
    else:
        lines = [f"- [[{source_link}]]"]
    return content.rstrip() + "\n\n## Source Evidence\n" + "\n".join(lines) + "\n"


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
    *,
    merge_sources: bool = False,
) -> None:
    evidence = _extract_generated_page_evidence(content, source_file)
    update_evidence_map(wiki_dir, page_path, evidence, merge_sources=merge_sources)


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
    """Delete generated pages whose only source is this document."""
    source_file = f"summaries/{doc_name}.md"
    removed: list[str] = []

    for subdir in sorted({
        "companies",
        "concepts",
        *_INVESTMENT_PAGE_SUBDIRS,
        *_LEGACY_INVESTMENT_PAGE_SUBDIRS,
    }):
        pages_dir = wiki_dir / subdir
        if not pages_dir.exists():
            continue
        for path in sorted(pages_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            sources = _frontmatter_source_entries(text)
            if sources == [source_file]:
                page_id = f"{subdir}/{path.stem}"
                path.unlink()
                removed.append(page_id)
            elif source_file in sources:
                cleaned = _clean_existing_generated_page_artifacts(text)
                if cleaned != text:
                    path.write_text(cleaned, encoding="utf-8")

    _remove_index_entries(wiki_dir, set(removed))
    return removed


def _add_related_link(wiki_dir: Path, concept_slug: str, doc_name: str, source_file: str) -> None:
    """Add a cross-reference link to an existing concept page (no LLM call)."""
    concepts_dir = wiki_dir / "concepts"
    path = concepts_dir / f"{concept_slug}.md"
    if not path.exists():
        return

    text = _clean_existing_generated_page_artifacts(path.read_text(encoding="utf-8"))
    link = f"[[summaries/{doc_name}]]"
    if link in text:
        path.write_text(text, encoding="utf-8")
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

    text = text.rstrip() + f"\n\nSee also: {link}"
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
        text = _clean_existing_generated_page_artifacts(path.read_text(encoding="utf-8"))
        if link in text:
            path.write_text(text, encoding="utf-8")
            continue
        if "## Related Documents" in text:
            text = text.replace("## Related Documents\n", f"## Related Documents\n- {link}\n", 1)
        else:
            text = text.rstrip() + f"\n\n## Related Documents\n- {link}\n"
        path.write_text(text, encoding="utf-8")

def _update_index(
    wiki_dir: Path, doc_name: str, concept_names: list[str],
    doc_brief: str = "", concept_briefs: dict[str, str] | None = None,
    company_names: list[str] | None = None,
    company_briefs: dict[str, str] | None = None,
    industry_names: list[str] | None = None,
    industry_briefs: dict[str, str] | None = None,
    theme_names: list[str] | None = None,
    theme_briefs: dict[str, str] | None = None,
    metric_names: list[str] | None = None,
    metric_briefs: dict[str, str] | None = None,
    risk_names: list[str] | None = None,
    risk_briefs: dict[str, str] | None = None,
    doc_type: str = "short",
) -> None:
    """Append document and generated page entries to index.md.

    When ``doc_brief`` or entries in page brief maps are provided, entries
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
    if industry_names is None:
        industry_names = []
    if industry_briefs is None:
        industry_briefs = {}
    if theme_names is None:
        theme_names = []
    if theme_briefs is None:
        theme_briefs = {}
    if metric_names is None:
        metric_names = []
    if metric_briefs is None:
        metric_briefs = {}
    if risk_names is None:
        risk_names = []
    if risk_briefs is None:
        risk_briefs = {}

    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "# Knowledge Base Index\n\n"
            "## Documents\n\n"
            "## Companies\n\n"
            "## Industries\n\n"
            "## Concepts\n\n"
            "## Explorations\n",
            encoding="utf-8",
        )

    lines = index_path.read_text(encoding="utf-8").split("\n")
    _ensure_index_section(lines, "## Documents", before_heading="## Companies")
    _ensure_index_section(lines, "## Companies", before_heading="## Industries")
    _ensure_index_section(lines, "## Industries", before_heading="## Concepts")
    _ensure_index_section(lines, "## Concepts", before_heading="## Explorations")

    doc_link = f"[[summaries/{doc_name}]]"
    if not _section_contains_link(lines, "## Documents", doc_link):
        doc_entry = f"- {doc_link} ({doc_type})"
        if doc_brief:
            doc_entry += f" - {doc_brief}"
        _insert_section_entry(lines, "## Documents", doc_entry)

    def upsert_page_entries(
        section: str,
        subdir: str,
        names: list[str],
        briefs: dict[str, str],
    ) -> None:
        for name in names:
            page_link = f"[[{subdir}/{name}]]"
            page_entry = f"- {page_link}"
            if name in briefs:
                page_entry += f" - {briefs[name]}"
            if _section_contains_link(lines, section, page_link):
                if name in briefs:
                    _replace_section_entry(lines, section, page_link, page_entry)
            else:
                _insert_section_entry(lines, section, page_entry)

    upsert_page_entries("## Companies", "companies", company_names, company_briefs)
    upsert_page_entries("## Industries", "industries", industry_names, industry_briefs)

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

DEFAULT_COMPILE_CONCURRENCY = 2
DEFAULT_LOCAL_LONG_PAGE_CHARS = 1800
DEFAULT_LOCAL_LONG_TOTAL_CHARS = 65000


def get_compile_max_concurrency(value: int | None = None) -> int:
    """Return a safe page-generation concurrency limit."""
    configured = os.getenv("OPENKB_COMPILE_MAX_CONCURRENCY", "").strip()
    if configured:
        try:
            return max(int(configured), 1)
        except ValueError:
            pass

    if value is not None:
        try:
            return max(int(value), 1)
        except (TypeError, ValueError):
            return DEFAULT_COMPILE_CONCURRENCY

    return DEFAULT_COMPILE_CONCURRENCY


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


def _pageindex_page_text(page: object) -> tuple[str, str]:
    """Return page number and text from a PageIndex source JSON entry."""
    if not isinstance(page, dict):
        return "?", ""
    page_num = str(page.get("page") or page.get("page_num") or page.get("number") or "?")
    for key in ("content", "text", "markdown"):
        value = page.get(key)
        if isinstance(value, str) and value.strip():
            return page_num, value.strip()
    return page_num, ""


def _build_pageindex_financial_evidence_pack(
    wiki_dir: Path,
    doc_name: str,
    summary_content: str,
    *,
    max_pages: int = 12,
    page_chars: int = 1400,
) -> str:
    """Select high-signal source pages for annual/financial report synthesis."""
    hint = f"{doc_name}\n{summary_content[:4000]}".casefold()
    if not any(keyword.casefold() in hint for keyword in _FINANCIAL_REPORT_DOC_HINTS):
        return ""

    source_path = wiki_dir / "sources" / f"{doc_name}.json"
    if not source_path.exists():
        return ""
    try:
        pages = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read PageIndex source JSON for %s: %s", doc_name, exc)
        return ""
    if not isinstance(pages, list):
        return ""

    scored: list[tuple[int, int, str, str]] = []
    for order, page in enumerate(pages):
        page_num, text = _pageindex_page_text(page)
        if not text:
            continue
        haystack = text.casefold()
        score = sum(
            haystack.count(keyword.casefold())
            for keyword in _FINANCIAL_REPORT_PAGE_KEYWORDS
        )
        if score <= 0:
            continue
        scored.append((score, order, page_num, text))

    if not scored:
        return ""

    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_pages]
    selected.sort(key=lambda item: item[1])
    lines = [
        "Additional source page context for annual/financial report synthesis:",
        "Use these pages for exact metrics and cite them as p.N. Do not write placeholders.",
    ]
    for _, _, page_num, text in selected:
        snippet = re.sub(r"\s+", " ", text).strip()
        if len(snippet) > page_chars:
            snippet = snippet[:page_chars].rstrip() + " [truncated]"
        lines.append(f"p.{page_num}: {snippet}")
    return "\n\n" + "\n".join(lines)


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
    write_lock=None,
) -> None:
    """Shared Steps 2-4: concepts plan → generate/update → index.

    Uses ``_CONCEPTS_PLAN_USER`` to get a plan with create/update/related
    actions, then executes each action type accordingly.
    """
    source_file = f"summaries/{doc_name}.md"
    registry = EntityRegistry.load(kb_dir)

    def _write_context():
        return write_lock if write_lock is not None else nullcontext()

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

    fallback_company_items = [
        canonical for item in _extract_company_candidates_from_summary(summary)
        for canonical in [_canonicalize_company_item(item)]
        if canonical is not None
    ]
    company_items: list[dict] = []
    if isinstance(company_parsed, dict):
        raw_company_items = company_parsed.get("companies", [])
        if isinstance(raw_company_items, list):
            company_items = [
                canonical for item in raw_company_items
                if isinstance(item, dict)
                for canonical in [_canonicalize_company_item(item)]
                if canonical is not None
            ]
    if not company_items:
        company_items = fallback_company_items
    company_items, resolved_company_entities = _resolve_company_items_against_registry(company_items, registry)
    company_keys = _company_alias_keys(company_items + fallback_company_items)
    company_keys.update(_registered_entity_alias_keys(registry, "company"))

    planned_company_slugs = {
        _sanitize_concept_name(str(item["name"]))
        for item in company_items
    }

    # --- Step 2b: Get dedicated investment page plan (A cached) ---
    investment_page_briefs = _read_investment_page_briefs(wiki_dir)
    investment_page_plan_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        {"role": "assistant", "content": summary},
        {"role": "user", "content": _INVESTMENT_PAGES_PLAN_USER.format(
            **investment_page_briefs,
        )},
    ], "investment-pages-plan", max_tokens=1536)

    try:
        investment_page_parsed = _parse_json(investment_page_plan_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse investment pages plan: %s", exc)
        logger.debug("Raw: %s", investment_page_plan_raw)
        investment_page_parsed = {}

    investment_page_plan = _parse_investment_page_plan(investment_page_parsed)
    investment_page_plan = _dedupe_investment_page_plan(wiki_dir, investment_page_plan)
    investment_page_plan, resolved_investment_entities = _resolve_investment_page_plan_against_registry(
        investment_page_plan,
        registry,
    )
    planned_investment_slugs = _planned_investment_page_slugs(investment_page_plan)

    # --- Step 2c: Get concepts plan (A cached) ---
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
    plan = _filter_concept_plan_against_registered_industries(plan, registry)
    plan = _ensure_summary_links_in_plan(wiki_dir, summary, plan)
    plan = _filter_concept_plan_against_companies(plan, company_keys)
    plan = _filter_concept_plan_against_registered_industries(plan, registry)
    plan = _dedupe_concept_plan(wiki_dir, plan)
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
    wiki_link_aliases = {
        **concept_aliases,
        **_registered_entity_link_aliases(registry),
    }
    allowed_concept_slugs = _planned_concept_slugs(create_items, update_items, related_items)
    valid_pages = (
        _known_wiki_pages(wiki_dir)
        | {f"companies/{slug}" for slug in planned_company_slugs}
        | {
            f"{subdir}/{slug}"
            for subdir, slugs in planned_investment_slugs.items()
            for slug in slugs
        }
        | {f"concepts/{slug}" for slug in allowed_concept_slugs}
        | {f"summaries/{doc_name}"}
    )
    summary = _normalize_wiki_links(summary, wiki_link_aliases, allowed_concept_slugs, valid_pages)
    with _write_context():
        _rewrite_summary_links(wiki_dir, doc_name, wiki_link_aliases, allowed_concept_slugs, valid_pages)

    investment_page_items = [
        (subdir, item)
        for subdir, items in investment_page_plan.items()
        for item in items
    ]

    if not company_items and not investment_page_items and not create_items and not update_items and not related_items:
        with _write_context():
            _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    # --- Step 3: Generate/update company and concept pages concurrently (A cached) ---
    semaphore = asyncio.Semaphore(max_concurrency)
    require_page_evidence = doc_type == "pageindex"

    def _should_skip_generated_page(page_kind: str, name: str, content: str) -> bool:
        issues = _generated_page_quality_issues(
            content,
            source_file,
            require_page_evidence=require_page_evidence,
        )
        if not issues:
            return False
        logger.warning(
            "Skipping generated %s page %s for %s: %s",
            page_kind,
            name,
            doc_name,
            "; ".join(issues),
        )
        return True

    def _clean_existing_generated_page(page_kind: str, name: str) -> None:
        if page_kind == "company":
            path = wiki_dir / "companies" / f"{_sanitize_concept_name(name)}.md"
        elif page_kind == "concept":
            path = wiki_dir / "concepts" / f"{_sanitize_concept_name(name)}.md"
        elif page_kind in _INVESTMENT_PAGE_SUBDIRS:
            path = wiki_dir / page_kind / f"{_sanitize_concept_name(name)}.md"
        else:
            return
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        cleaned = _clean_existing_generated_page_artifacts(text)
        if cleaned != text:
            path.write_text(cleaned, encoding="utf-8")

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
                "Use the existing content only as context. Focus the returned "
                "content on the new document's company-specific evidence; "
                "OpenKB will merge it without deleting prior source evidence."
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
        content = _ensure_h1(content, title)
        return name, content, is_update, brief

    async def _gen_investment_page(subdir: str, page: dict) -> tuple[str, str, str, bool, str]:
        page_type = _INVESTMENT_PAGE_TYPE_BY_SUBDIR[subdir]
        name = str(page["name"]).strip()
        title = str(page.get("title", name)).strip() or name
        safe_name = _sanitize_concept_name(name)
        page_path = wiki_dir / subdir / f"{safe_name}.md"
        requested_action = str(page.get("action", "")).lower()
        is_update = requested_action == "update" or page_path.exists()
        if is_update and page_path.exists():
            raw_text = page_path.read_text(encoding="utf-8")
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                existing_content = parts[2].strip() if len(parts) >= 3 else raw_text
            else:
                existing_content = raw_text
            update_instruction = (
                f"Current content of this {page_type['label']} page:\n"
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
                {"role": "user", "content": _INVESTMENT_PAGE_USER.format(
                    page_label=page_type["label"],
                    title=title,
                    doc_name=doc_name,
                    update_instruction=update_instruction,
                    page_guidance=page_type["guidance"],
                )},
            ], f"{page_type['label']}: {name}")
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            content = parsed.get("content", raw)
        except (json.JSONDecodeError, ValueError):
            brief, content = "", raw
        content = _ensure_h1(content, title)
        return subdir, name, content, is_update, brief

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
        content = _ensure_h1(content, title)
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
        content = _ensure_h1(content, title)
        return name, content, True, brief

    company_tasks = [_gen_company(c) for c in company_items]
    investment_page_tasks = [
        _gen_investment_page(subdir, item)
        for subdir, item in investment_page_items
    ]

    tasks = []
    tasks.extend(_gen_create(c) for c in create_items)
    tasks.extend(_gen_update(c) for c in update_items)

    company_names: list[str] = []
    company_briefs_map: dict[str, str] = {}
    investment_page_names: dict[str, list[str]] = {
        subdir: [] for subdir in _INVESTMENT_PAGE_SUBDIRS
    }
    investment_page_briefs_map: dict[str, dict[str, str]] = {
        subdir: {} for subdir in _INVESTMENT_PAGE_SUBDIRS
    }
    concept_names: list[str] = []
    concept_briefs_map: dict[str, str] = {}

    company_results = []
    investment_page_results = []
    concept_results = []

    if company_tasks:
        total = len(company_tasks)
        sys.stdout.write(f"    Generating {total} company page(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()

        company_results = await asyncio.gather(*company_tasks, return_exceptions=True)

    if investment_page_tasks:
        total = len(investment_page_tasks)
        sys.stdout.write(f"    Generating {total} dedicated investment page(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()

        investment_page_results = await asyncio.gather(*investment_page_tasks, return_exceptions=True)

    if tasks:
        total = len(tasks)
        sys.stdout.write(f"    Generating {total} concept(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()

        concept_results = await asyncio.gather(*tasks, return_exceptions=True)

    with _write_context():
        for r in company_results:
            if isinstance(r, Exception):
                logger.warning("Company generation failed: %s", r)
                continue
            name, page_content, is_update, brief = r
            page_content = _normalize_wiki_links(
                page_content,
                wiki_link_aliases,
                allowed_concept_slugs,
                valid_pages,
            )
            if _should_skip_generated_page("company", name, page_content):
                _clean_existing_generated_page("company", name)
                continue
            index_brief = _write_company(wiki_dir, name, page_content, source_file, is_update, brief=brief)
            safe_name = _sanitize_concept_name(name)
            company_names.append(safe_name)
            if index_brief:
                company_briefs_map[safe_name] = index_brief

        for r in investment_page_results:
            if isinstance(r, Exception):
                logger.warning("Investment page generation failed: %s", r)
                continue
            subdir, name, page_content, is_update, brief = r
            page_content = _normalize_wiki_links(
                page_content,
                wiki_link_aliases,
                allowed_concept_slugs,
                valid_pages,
            )
            if _should_skip_generated_page(subdir, name, page_content):
                _clean_existing_generated_page(subdir, name)
                continue
            _write_investment_page(wiki_dir, subdir, name, page_content, source_file, is_update, brief=brief)
            safe_name = _sanitize_concept_name(name)
            investment_page_names[subdir].append(safe_name)
            if brief:
                investment_page_briefs_map[subdir][safe_name] = brief

        for r in concept_results:
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
                wiki_link_aliases,
                allowed_concept_slugs,
                valid_pages,
            )
            if _should_skip_generated_page("concept", name, page_content):
                _clean_existing_generated_page("concept", name)
                continue
            index_brief = _write_concept(wiki_dir, name, page_content, source_file, is_update, brief=brief)
            safe_name = _sanitize_concept_name(name)
            concept_names.append(safe_name)
            if index_brief:
                concept_briefs_map[safe_name] = index_brief

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
        final_aliases.update(_registered_entity_link_aliases(registry))
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
                merge_sources=True,
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
                merge_sources=True,
            )
        for subdir, names in investment_page_names.items():
            for slug in names:
                page_path = wiki_dir / subdir / f"{slug}.md"
                if not page_path.exists():
                    continue
                text = page_path.read_text(encoding="utf-8")
                normalized_text = _normalize_wiki_links(
                    text,
                    final_aliases,
                    successful_concept_slugs,
                    final_valid_pages,
                )
                if normalized_text != text:
                    page_path.write_text(normalized_text, encoding="utf-8")
                    text = normalized_text
                _record_generated_page_evidence(
                    wiki_dir,
                    f"{subdir}/{slug}.md",
                    text,
                    source_file,
                    merge_sources=True,
                )

        all_concept_slugs = concept_names + sanitized_related
        if all_concept_slugs:
            _backlink_summary(wiki_dir, doc_name, all_concept_slugs)
            _backlink_concepts(wiki_dir, doc_name, all_concept_slugs)

        # --- Step 4: Update index (code only) ---
        _update_index(wiki_dir, doc_name, concept_names,
                      doc_brief=doc_brief, concept_briefs=concept_briefs_map,
                      company_names=company_names, company_briefs=company_briefs_map,
                      industry_names=investment_page_names["industries"],
                      industry_briefs=investment_page_briefs_map["industries"],
                      doc_type=doc_type)


async def _compile_short_doc_to_wiki(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    wiki_dir: Path,
    model: str,
    max_concurrency: int | None = None,
) -> None:
    """Compile a short document using a multi-step LLM pipeline with caching.

    Step 1: Build base context A (schema + doc content), generate summary.
    Steps 2-4: Delegated to ``_compile_concepts``.
    """
    from openkb.config import load_config

    max_concurrency = get_compile_max_concurrency(max_concurrency)

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
    max_concurrency: int | None = None,
    cleanup_existing: bool = False,
) -> list[str]:
    """Compile a short document and commit generated wiki files atomically."""
    max_concurrency = get_compile_max_concurrency(max_concurrency)

    async def operation(staged_wiki: Path) -> list[str]:
        removed = cleanup_generated_pages_for_source(staged_wiki, doc_name) if cleanup_existing else []
        await _compile_short_doc_to_wiki(
            doc_name,
            source_path,
            kb_dir,
            staged_wiki,
            model,
            max_concurrency,
        )
        return removed

    return await _run_with_staged_wiki(kb_dir, operation)


async def _compile_local_long_doc_to_wiki(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    wiki_dir: Path,
    model: str,
    max_concurrency: int | None = None,
) -> None:
    """Compile a long PDF converted to local page JSON."""
    from openkb.config import load_config

    max_concurrency = get_compile_max_concurrency(max_concurrency)

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
    max_concurrency: int | None = None,
    cleanup_existing: bool = False,
) -> list[str]:
    """Compile a local-long document and commit generated wiki files atomically."""
    max_concurrency = get_compile_max_concurrency(max_concurrency)

    async def operation(staged_wiki: Path) -> list[str]:
        removed = cleanup_generated_pages_for_source(staged_wiki, doc_name) if cleanup_existing else []
        await _compile_local_long_doc_to_wiki(
            doc_name,
            source_path,
            kb_dir,
            staged_wiki,
            model,
            max_concurrency,
        )
        return removed

    return await _run_with_staged_wiki(kb_dir, operation)


async def _compile_long_doc_to_wiki(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    wiki_dir: Path,
    model: str,
    doc_description: str = "",
    max_concurrency: int | None = None,
) -> None:
    """Compile a long (PageIndex) document's concepts and index.

    The summary page is already written by the indexer. This function
    generates concept pages and updates the index.
    """
    from openkb.config import load_config

    max_concurrency = get_compile_max_concurrency(max_concurrency)

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
    long_doc_context = summary_content + _build_pageindex_financial_evidence_pack(
        wiki_dir,
        doc_name,
        summary_content,
    )

    # Base context A
    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _LONG_DOC_SUMMARY_USER.format(
        doc_name=doc_name, doc_id=doc_id, content=long_doc_context,
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
    max_concurrency: int | None = None,
    cleanup_existing: bool = False,
) -> list[str]:
    """Compile a PageIndex long document and commit generated wiki files atomically."""
    max_concurrency = get_compile_max_concurrency(max_concurrency)

    async def operation(staged_wiki: Path) -> list[str]:
        removed = cleanup_generated_pages_for_source(staged_wiki, doc_name) if cleanup_existing else []
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
        return removed

    return await _run_with_staged_wiki(kb_dir, operation)
