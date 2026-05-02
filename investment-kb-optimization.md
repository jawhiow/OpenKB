# Investment Knowledge Base Optimization

> Status document for the OpenKB investment research knowledge-base work.

## Background

The target workflow is a long-term investment knowledge base inspired by
Karpathy-style knowledge compounding: raw research reports are compiled into a
durable Markdown wiki with summaries, reusable concept pages, evidence links,
and queryable page-level source context.

The first test KBs were generated from a Morgan Stanley Greater China
semiconductor report focused on AI-related semiconductors, including GPU,
ASIC, CPU, optical chips, advanced packaging, memory, testing equipment, and
China AI supply-chain localization.

## Problems Observed

### openkb-test

- Generated output was too thin for long-term investment use.
- The summary was short relative to the source report.
- Only a few concept pages were produced.
- Summary links such as `[[concepts/...]]` could point to pages that were not
  actually created.
- Long PDFs fell back to short Markdown conversion when PageIndex was not
  usable.

### PageIndex Availability

The installed `pageindex` package is a cloud API client:

- `PageIndexClient.__init__(api_key: str)`
- Base URL: `https://api.pageindex.ai`
- It does not accept the configured OpenAI-compatible LLM model or base URL.

Therefore `PAGEINDEX_API_KEY` is separate from `LLM_API_KEY` or
`OPENAI_API_KEY`. Without `PAGEINDEX_API_KEY`, OpenKB must use a local long-doc
path.

### openkb-test2

Regeneration exposed more serious quality issues:

- Summary was generated, but concepts were missing when the concept-plan LLM
  response was not valid JSON.
- After fallback was added, too many company-name links were treated as durable
  concepts, causing noisy company concept pages.
- Interrupted or slow LLM runs could leave partial wiki state.
- Bare links such as `[[台积电]]` or unresolved company links could create
  broken links or pollute `concepts/`.

## Completed Optimizations

- [x] Added local long-PDF fallback when PageIndex is unavailable.
  - Long PDFs are converted to page-indexed JSON.
  - Page numbers and extracted image references are preserved.
  - CLI registers these as `local_long_pdf`, displayed as `local-long`.

- [x] Added `compile_local_long_doc`.
  - Uses page-indexed local JSON as prompt context.
  - Writes summaries with `doc_type: local-long`.
  - Query agent knows to use page-range retrieval for these documents.

- [x] Strengthened investment research prompts.
  - Summary prompts now ask for thesis, ratings, valuation context, forecasts,
    catalysts, risks, disconfirming evidence, monitoring indicators, and page
    evidence.
  - Concept prompts now ask for durable investment knowledge rather than generic
    explanation.

- [x] Normalized concept links.
  - Summary `[[concepts/...]]` links are represented in the concept plan.
  - Known aliases are rewritten to canonical concept slugs.
  - Unknown concept links can be unlinked instead of becoming broken links.

- [x] Added `openkb add --force`.
  - Existing documents can be recompiled without manually editing
    `.openkb/hashes.json`.

- [x] Fixed concept-plan parse failure behavior.
  - Invalid plan JSON no longer immediately skips concept handling.
  - Small numbers of summary concept links can be used as a fallback plan.

- [x] Prevented fallback concept explosion.
  - If a failed plan leaves too many unplanned concept links, OpenKB treats that
    as likely model over-linking and unlinks them instead of creating dozens of
    noisy pages.

- [x] Cleaned unresolved wiki links.
  - Bare unresolved wiki links are downgraded to plain text.
  - Failed concept generation no longer leaves broken summary links.

## Current Quality Bar

A generated investment KB should satisfy:

- The summary preserves key ratings, company exposure, estimates, valuation
  context, risks, and monitoring indicators.
- Important claims should have page references when available.
- `index.md` should list every summary and durable concept page.
- `concepts/` should contain reusable investment themes, mechanisms, metrics,
  risks, and industry structures.
- Company pages should not be mixed into `concepts/` long term; they need a
  separate `companies/` schema.
- Lint should report broken links, missing index entries, and investment-KB
  quality problems.

## Optimization Roadmap

- [x] Investment KB quality lint.
  - Detect concept explosion from one document.
  - Detect company-like pages under `concepts/`.
  - Surface these in the existing lint report.
  - Completed in this pass as report-only checks in `openkb.lint`.

- [ ] Dedicated investment schema.
  - Add `companies/`, `industries/`, `themes/`, `metrics/`, and `risks/`.
  - Update wiki schema and query instructions.

- [ ] Company extraction and routing.
  - Company-specific pages should go to `companies/`, not `concepts/`.
  - Concept pages should link to company pages only when those pages exist.

- [ ] Atomic compile writes.
  - Write summaries and concepts through a staging area.
  - Commit generated output only after the full compile succeeds.

- [ ] Source-backed evidence map.
  - Track important claims to page references in a structured way.
  - Let query responses cite exact source pages more reliably.

- [ ] Investment rebuild command.
  - Provide a safer `openkb rebuild` or `openkb upgrade-wiki` flow for existing
    KBs.

## Verification History

- `python -m pytest tests/test_compiler.py -q` passed after concept-link fixes.
- `python -m pytest -q` passed with `271 passed` after the latest compiler
  quality fixes.
- `python -m pytest tests/test_lint.py::TestInvestmentQualityIssues
  tests/test_lint.py::TestRunStructuralLint::test_report_includes_investment_quality_section -q`
  passed after adding investment-specific lint.
- `python -m pytest tests/test_lint.py tests/test_lint_cli.py tests/test_linter.py -q`
  passed with `37 passed`.
- `python -m pytest tests/test_compiler.py -q` passed with `64 passed`.
- `python -m pytest -q` passed with `274 passed` after investment quality lint.

