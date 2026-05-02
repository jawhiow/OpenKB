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

### openkb-test3

After regenerating through the local client, the new KB was materially better:

- Structural lint reported no broken links, orphan pages, missing files, or
  index sync issues.
- Investment quality lint no longer showed concept explosion.
- The generated concepts were durable themes such as AI ASIC, China AI GPU,
  Cloud CAPEX, CoWoS, HBM, and semiconductor testing.

Remaining gaps:

- Investment quality lint initially misclassified CoWoS and HBM as company
  pages because their concept pages had "Company Exposure" sections.
- `openkb lint` could crash on Windows GBK consoles while printing emoji or
  non-GBK semantic lint text.
- Semantic lint still flagged missing coverage for AI CPU, optical chips / CPO,
  SoIC, export controls, non-AI semiconductor cycle, and specialty memory.
- Company-specific evidence was still mixed into summaries and concept pages;
  the KB needed a dedicated company-page route.

Latest regeneration status after the additional routing fixes:

- `openkb add --force` on `D:\知识库\openkb-test3` now removes stale generated
  company/concept pages for the same source before recompiling.
- The regenerated KB has 7 company pages:
  `alchip`, `ap-memory`, `aspeed`, `hon-precision`, `macronix`, `mediatek`,
  and `tsmc`.
- The regenerated KB has 10 concept pages:
  `Advanced_Packaging`, `AI_ASIC`, `China_AI_GPU`, `CoWoS`, `CPO`, `HBM`,
  `NOR_Flash`, `Optical_Engines`, `Semiconductor_Testing`, and `SoIC`.
- Deterministic structural lint on `openkb-test3` is clean:
  broken links 0, orphan pages 0, raw files without wiki entry 0, index sync
  issues 0, investment quality issues 0.
- Semantic lint still found higher-order knowledge gaps, especially
  `Cloud_CAPEX`, `AI_CPU`, global `AI_GPU`, `Export_Controls`, and
  `Semiconductor_Cycle`.

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

- [x] Fixed investment quality lint false positives.
  - Company-like concept detection now focuses on the intro section instead of
    later "Company Exposure" sections.
  - Durable theme pages such as CoWoS and HBM are no longer flagged as company
    pages solely because they discuss exposed companies.

- [x] Made lint output Windows-safe.
  - CLI lint output uses a safe echo path that replaces unencodable console
    characters instead of crashing.
  - This preserves report generation even when semantic lint includes emoji or
    characters outside the active Windows console code page.

- [x] Added `companies/` as the first dedicated investment entity route.
  - `openkb init` creates `wiki/companies`.
  - `index.md`, `AGENTS.md`, `list`, `status`, query instructions, and semantic
    lint instructions now recognize company pages.
  - Structural lint includes company pages in index-sync checks.

- [x] Added company extraction and routing to compilation.
  - Compilation now asks for a company plan before concept planning.
  - Company-specific investment pages are generated under `companies/`.
  - Company entries are written to the `## Companies` index section with briefs.
  - Concept pages remain reserved for reusable mechanisms, risks, metrics,
    themes, and industry structures.

- [x] Added fallback company extraction.
  - If the company-plan LLM response is invalid or returns an empty company
    list, OpenKB extracts high-signal company names from investment summary
    lines such as top picks, overweight lists, and main beneficiaries.
  - This fixed the real `openkb-test3` case where company planning failed but
    the summary clearly contained company evidence.

- [x] Added stale generated page cleanup for `openkb add --force`.
  - Before recompiling, OpenKB deletes generated `companies/` and `concepts/`
    pages whose only source is the current summary.
  - Pages with multiple sources are preserved so long-term accumulated
    knowledge is not destroyed.
  - Matching index rows are removed before fresh entries are written.

- [x] Added company-name filtering for concept plans.
  - If the concept plan tries to create companies such as TSMC, MPI, WinWay, or
    Hon Precision under `concepts/`, those items are filtered out.
  - Generic aliases such as `ASIC` are canonicalized to durable concepts such
    as `AI_ASIC`.

- [x] Added investment concept fallback for failed concept plans.
  - When concept-plan JSON is invalid and the summary does not already contain
    explicit `[[concepts/...]]` links, OpenKB can derive durable concepts from
    investment report headings.
  - Current fallback covers stable topics already validated in `openkb-test3`,
    including advanced packaging, CoWoS, SoIC, AI ASIC, HBM, NOR Flash, China
    AI GPU, semiconductor testing, optical engines, and CPO.

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

- [x] Company-page investment schema.
  - Add `companies/`.
  - Update wiki schema, query instructions, semantic lint instructions, CLI
    listing/status, and structural index checks.

- [x] Company extraction and routing foundation.
  - Company-specific pages go to `companies/`, not `concepts/`.
  - Concept and company page links are preserved only when target pages exist.

- [ ] Expanded investment schema.
  - Add `industries/`, `themes/`, `metrics/`, and `risks/` only where they
    remove real ambiguity beyond company and concept pages.

- [ ] Coverage-gap generation.
  - Use semantic lint findings to propose or generate missing durable pages for
    gaps such as AI CPU, optical chips / CPO, SoIC, export controls, non-AI
    semiconductor cycle, and specialty memory.
  - Current in-progress RED test expects fallback extraction for
    `Cloud_CAPEX`, `AI_CPU`, `AI_GPU`, `Export_Controls`, and
    `Semiconductor_Cycle`. Implementation is not finished yet.

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
- `python -m pytest tests/test_compiler.py -q` passed with `68 passed` after
  company-page extraction and routing.
- `python -m pytest -q` passed with `291 passed` after stale-page cleanup,
  company fallback, concept fallback, and company-name concept filtering.
- Current interrupted state: after the `291 passed` run, a new RED test was
  added for broader coverage-gap fallback extraction:
  `tests/test_compiler.py::TestConceptFallbackExtraction::test_extracts_macro_cpu_gpu_and_policy_concepts`.
  It currently fails because `AI_CPU` and global `AI_GPU` extraction have not
  been implemented yet. Do not treat the current workspace as fully green until
  that test is implemented and the suite is rerun.
