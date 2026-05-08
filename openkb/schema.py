from __future__ import annotations

from pathlib import Path

ACTIVE_WIKI_CONTENT_DIRS = ("summaries", "companies", "industries", "concepts", "explorations")
LEGACY_WIKI_DIRS = ("themes", "metrics", "risks")
LEGACY_WIKI_GUIDANCE = (
    "Ignore deprecated legacy directories `themes/`, `metrics/`, and `risks/` "
    "if they still exist; that content now belongs under `concepts/`."
)

AGENTS_MD = """\
# Wiki Schema

## Directory Structure
- sources/ - Document content. Short docs as .md, long docs as .json (per-page). Do not modify directly.
- sources/images/ - Extracted images from documents, referenced by sources.
- summaries/ - One per source document. Summary of key content.
- companies/ - Company-specific investment pages with ratings, valuation context, exposures, catalysts, risks, and source evidence. A company page must be an actual company or clearly named investable business.
- industries/ - Industry structure pages for sectors, value chains, capacity cycles, and competitive maps. An industry page must be a real industry, sector, or durable value-chain segment.
- concepts/ - Cross-document topic synthesis for reusable concepts, themes, risks, metrics, mechanisms, indicators, frameworks, and monitoring ideas.
- explorations/ - Saved query results, analyses, and comparisons worth keeping.
- reports/ - Lint health check reports. Auto-generated.

## Special Files
- index.md - Content catalog: every page with link, one-line summary, organized by category.
- log.md - Chronological append-only record of operations (ingests, queries, lints).

## Page Types
- **Summary Page** (summaries/): Key content of a single source document.
- **Company Page** (companies/): Company-specific investment evidence, not generic concepts, products, tickers, indexes, themes, or industries.
- **Industry Page** (industries/): Sector structure, value-chain position, capacity cycles, and competitive dynamics, not a company, product, risk, metric, geography, or one-off theme.
- **Concept Page** (concepts/): Cross-document topic synthesis with [[wikilinks]], including reusable themes, risks, metrics, frameworks, and mechanisms.
- **Exploration Page** (explorations/): Saved query results, analyses, comparisons, syntheses.
- **Index Page** (index.md): One-liner summary of every page in the wiki. Auto-maintained.

## Index Page Format
index.md lists all documents, companies, industries, concepts, and explorations with metadata:
- Documents: name, one-liner description, type (short|pageindex|local-long), detail access path
- Companies: company name, one-liner investment relevance
- Industries: industry or value-chain segment, one-liner structure/relevance
- Concepts: name, one-liner description
- Explorations: name, one-liner description

## Investment Research Guidance
For broker research, earnings notes, industry reports, or other investment documents:
- Preserve key companies, ratings, target prices, dates, forecasts, valuation context, and units.
- Capture the investment thesis, catalysts, risks, disconfirming evidence, and monitoring indicators.
- Prefer durable concept pages for reusable cross-document themes, risks, metrics, mechanisms, indicators, frameworks, and monitoring ideas.
- Route company-specific claims to `companies/` pages only when the page subject must be an actual company or clearly named investable business; keep claims traceable to the source summary or page evidence.
- Route industry structure to `industries/` only when the page subject must be a real industry, sector, or durable value-chain segment.
- If a candidate is a theme, risk, metric, product, technology, geography, policy, event, thesis, or monitoring signal, use `concepts/` rather than a dedicated directory.
- Do not create company pages under `concepts/`; concepts should stay reusable and non-company-specific.
- Do not create industry pages for companies, products, tickers, indexes, risks, metrics, geographies, events, or one-off themes.

## Log Format
Each log entry: `## [YYYY-MM-DD HH:MM:SS] operation | description`
Operations: ingest, query, lint

## Format
- Use [[wikilink]] to link other wiki pages (e.g., [[concepts/attention]])
- Standard Markdown heading hierarchy
- Keep each page focused on a single topic
- Do not include YAML frontmatter (---) in generated content; it is managed by code
"""

# Backward compat alias
SCHEMA_MD = AGENTS_MD


def get_agents_md(wiki_dir: Path) -> str:
    """Return the AGENTS.md content, reading from disk if available.

    Args:
        wiki_dir: Path to the wiki directory (containing AGENTS.md).

    Returns:
        Content of wiki_dir/AGENTS.md if it exists, otherwise the hardcoded
        AGENTS_MD default.
    """
    agents_file = wiki_dir / "AGENTS.md"
    if agents_file.exists():
        return agents_file.read_text(encoding="utf-8")
    return AGENTS_MD
