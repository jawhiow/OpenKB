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
  `Semiconductor_Cycle`. The local concept fallback can now extract these
  five concepts when summary text contains the relevant investment signals.
  `openkb lint` can also surface them as coverage-gap concept candidates when
  the semantic lint report flags the topics as missing or uncovered. With
  `openkb lint --fix`, those candidates can now be materialized as reviewable
  draft concept pages without overwriting existing pages, seeded with matching
  source-summary evidence when available. For local-long summaries that point
  to page-indexed JSON, draft evidence can now include page references such as
  `p.2`.
  Coverage-gap draft evidence is also written to `wiki/evidence_map.json` so
  query/rebuild flows can reuse structured source and page references.
  Regular compile output now also records structured evidence for generated
  `concepts/` and `companies/` pages when their source-evidence lines contain
  page references such as `p.7` or `page 12`. Generated summary pages now do
  the same for their own page-referenced claims, pointing back to the
  underlying `sources/<doc>.md` or `sources/<doc>.json` file.
  Compile writes now run through a staging wiki and only commit generated
  summaries, companies, concepts, index rows, and evidence-map changes after
  the compile operation succeeds.
  Existing KBs can now be rebuilt from `raw/` through `openkb rebuild`, which
  reuses the safe `add --force` path for every supported raw document.
  The expanded investment schema now recognizes `industries/`, `themes/`,
  `metrics/`, and `risks/` as first-class optional wiki areas without forcing
  the compiler to route pages there prematurely.

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

- [x] Added semantic coverage-gap candidate reporting.
  - `openkb lint` now scans semantic lint report sections such as gaps,
    coverage, missing pages, and Chinese missing-coverage phrasing.
  - It maps recognized investment gaps to durable `concepts/` candidates,
    filters out concept pages that already exist, and writes a
    `Coverage Gap Candidates` section into the lint report.

- [x] Added safe coverage-gap draft generation.
  - `openkb lint --fix` now creates draft `concepts/` pages for semantic
    coverage-gap candidates.
  - Draft pages use `status: draft`, empty `sources: []`, and TODO sections for
    source evidence, key metrics, risks, and related concepts.
  - Existing concept pages are never overwritten, and new draft pages are added
    to the `## Concepts` section of `index.md`.

- [x] Seeded coverage-gap drafts with source-summary evidence.
  - When `openkb lint --fix` creates a draft concept page, it scans existing
    `summaries/` pages for matching fallback concept signals.
  - Matching summaries are added to draft frontmatter `sources: [...]`.
  - The `## Source Evidence` section starts with `[[summaries/...]]` links and
    a short snippet instead of a blank TODO when evidence is available.

- [x] Added page-level evidence for local-long coverage-gap drafts.
  - If a matching summary has `full_text: sources/<doc>.json`, `openkb lint
    --fix` scans the page-indexed JSON for the same durable concept signal.
  - Draft `## Source Evidence` entries now prefer `[[summaries/...]] p.N:
    snippet` when a matching page is found.
  - This keeps draft pages reviewable while preserving tighter source context.

- [x] Added a structured evidence map for coverage-gap drafts.
  - `openkb lint --fix` now writes `wiki/evidence_map.json` entries for draft
    concept pages that have matching summary or page evidence.
  - Each evidence map entry includes the concept page path, source summary,
    page number when available, and a short snippet.
  - The query agent instructions now tell OpenKB to read `evidence_map.json`
    when exact source support is needed.

- [x] Extended the evidence map to generated concept and company pages.
  - During normal compilation, generated `concepts/` and `companies/` pages are
    scanned after final link normalization for source-evidence lines with page
    references such as `p.N` or `page N`.
  - Matching evidence is written to `wiki/evidence_map.json` with the generated
    page path, source summary, page number, and cleaned claim snippet.
  - Coverage-gap fixes and compiler output now share the same evidence-map
    writer instead of maintaining separate JSON write logic.

- [x] Added summary-page evidence map entries.
  - `_write_summary` now records page-referenced summary claims into
    `wiki/evidence_map.json`.
  - Summary entries are keyed by `summaries/<doc>.md`, point to the underlying
    `sources/<doc>.md` or `sources/<doc>.json`, and preserve the page number and
    cleaned snippet.
  - This completes structured evidence-map coverage for normal generated
    summaries, concept pages, company pages, and coverage-gap drafts.

- [x] Added atomic compile staging for generated wiki output.
  - `compile_short_doc`, `compile_local_long_doc`, and `compile_long_doc` now
    compile against a staged copy under `.openkb/staging`.
  - On success, only compile-managed wiki outputs are synced back:
    `summaries/`, `companies/`, `concepts/`, `index.md`, and
    `evidence_map.json`.
  - On failure, the staging directory is removed and the real wiki is left
    unchanged, preventing half-written summaries or evidence maps after a later
    planning/generation error.

- [x] Added an investment KB rebuild command.
  - `openkb rebuild` scans `raw/` for supported documents and recompiles each
    one through `add_single_file(..., force=True)`.
  - The command reuses existing conversion, stale-page cleanup, staged compile,
    hash registration, and logging behavior instead of inventing a separate
    rebuild path.
  - `openkb rebuild --strict` passes `strict=True` into the helper so automated
    rebuild jobs can fail fast on the first document error.

- [x] Added the optional expanded investment schema.
  - `openkb init` and client-side KB initialization now create `industries/`,
    `themes/`, `metrics/`, and `risks/` directories and index sections.
  - `AGENTS.md`, query instructions, semantic lint instructions, `list`,
    `status`, client status/document data, and structural index-sync checks now
    recognize those directories.
  - The compiler preserves these sections in `index.md`, but page routing stays
    conservative: regular generation still uses `companies/` and `concepts/`
    unless a future task adds dedicated extraction with evidence.

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

- [x] Expanded investment schema.
  - Add `industries/`, `themes/`, `metrics/`, and `risks/` only where they
    remove real ambiguity beyond company and concept pages.
  - Current implementation makes these optional directories first-class in the
    wiki schema, UI/status surfaces, query/lint instructions, and structural
    lint without adding noisy automatic extraction.

- [x] Coverage-gap generation.
  - Use semantic lint findings to propose or generate missing durable pages for
    gaps such as AI CPU, optical chips / CPO, SoIC, export controls, non-AI
    semiconductor cycle, and specialty memory.
  - Current fallback extraction now covers `Cloud_CAPEX`, `AI_CPU`, global
    `AI_GPU`, `Export_Controls`, and `Semiconductor_Cycle`.
  - Semantic lint coverage gaps are now connected to report-level durable page
    candidates.
  - `openkb lint --fix` can now turn candidates into reviewable draft concept
    pages.
  - Draft pages are now seeded with matching summary evidence where available.
  - Local-long draft evidence can now include page references from page-indexed
    JSON.
  - Structured evidence tracking has started to generalize beyond drafts into
    normally generated concept and company pages.
  - Broader existing-KB regeneration is now supported through `openkb rebuild`.

- [x] Atomic compile writes.
  - Write summaries and concepts through a staging area.
  - Commit generated output only after the full compile succeeds.
  - Current implementation stages all normal compile paths and commits only
    managed generated wiki outputs after success.

- [x] Source-backed evidence map.
  - Track important claims to page references in a structured way.
  - Let query responses cite exact source pages more reliably.
  - Current progress: coverage-gap draft pages now write structured evidence
    into `wiki/evidence_map.json`, generated concept/company pages now add
    page-reference evidence when the generated page includes `p.N` / `page N`
    source evidence, generated summary pages now record their own page-backed
    claims, and the query agent is aware of the map.
  - Future integration work belongs with the rebuild/upgrade flow so existing
    KBs can backfill or refresh the map safely.

- [x] Investment rebuild command.
  - Provide a safer `openkb rebuild` or `openkb upgrade-wiki` flow for existing
    KBs.
  - Current implementation provides `openkb rebuild` over `raw/`, using the
    existing force-recompile path and strict mode when requested.

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
- `.\.venv\Scripts\python.exe -m pytest
  tests/test_compiler.py::TestConceptFallbackExtraction -q` passed with
  `2 passed` after adding `AI_CPU` and global `AI_GPU` fallback extraction.
- `.\.venv\Scripts\python.exe -m pytest tests/test_compiler.py -q` passed with
  `76 passed` after the broader coverage-gap fallback extraction update.
- `.\.venv\Scripts\python.exe -m pytest -q` passed with `292 passed` after the
  coverage-gap fallback extraction update.
- `.\.venv\Scripts\python.exe -m pytest tests/test_linter.py tests/test_lint_cli.py
  tests/test_lint.py -q` passed with `44 passed` after adding semantic
  coverage-gap candidate reporting.
- `.\.venv\Scripts\python.exe -m pytest -q` passed with `295 passed` after the
  semantic coverage-gap candidate reporting update.
- `.\.venv\Scripts\python.exe -m pytest tests/test_linter.py tests/test_lint_cli.py
  tests/test_lint.py -q` passed with `47 passed` after adding safe
  `openkb lint --fix` coverage-gap draft generation.
- `.\.venv\Scripts\python.exe -m pytest -q` passed with `298 passed` after the
  safe coverage-gap draft generation update.
- `.\.venv\Scripts\python.exe -m pytest tests/test_linter.py tests/test_lint_cli.py
  tests/test_lint.py -q` passed with `48 passed` after seeding coverage-gap
  drafts with matching summary evidence.
- `.\.venv\Scripts\python.exe -m pytest -q` passed with `299 passed` after the
  source-summary evidence seeding update.
- `.\.venv\Scripts\python.exe -m pytest tests/test_linter.py tests/test_lint_cli.py
  tests/test_lint.py -q` passed with `49 passed` after adding local-long
  page-level evidence to coverage-gap drafts.
- `.\.venv\Scripts\python.exe -m pytest -q` passed with `300 passed` after the
  local-long page-level evidence update.
- `.\.venv\Scripts\python.exe -m pytest tests/test_linter.py tests/test_lint_cli.py
  tests/test_lint.py tests/test_query.py -q` passed with `62 passed` after
  adding `wiki/evidence_map.json` support for coverage-gap drafts.
- `.\.venv\Scripts\python.exe -m pytest
  tests/test_compiler.py::TestCompileConceptsPlan::test_generated_company_and_concept_pages_update_evidence_map
  -q` first failed because generated concept/company pages did not create
  `wiki/evidence_map.json`, then passed after compiler evidence-map support was
  added.
- `.\.venv\Scripts\python.exe -m pytest
  tests/test_compiler.py::TestCompileConceptsPlan
  tests/test_linter.py::TestCoverageGapCandidates tests/test_lint_cli.py
  tests/test_query.py -q` passed with `38 passed` after extending the evidence
  map to generated concept and company pages.
- `.\.venv\Scripts\python.exe -m pytest
  tests/test_compiler.py::TestWriteSummary::test_writes_summary_page_references_to_evidence_map
  tests/test_compiler.py::TestCompileConceptsPlan::test_generated_company_and_concept_pages_update_evidence_map
  -q` first failed for missing summary evidence-map output, then passed after
  summary page-reference extraction was added.
- `.\.venv\Scripts\python.exe -m pytest tests/test_compiler.py
  tests/test_linter.py::TestCoverageGapCandidates tests/test_lint_cli.py
  tests/test_query.py -q` passed with `104 passed` after adding summary-page
  evidence map entries.
- `.\.venv\Scripts\python.exe -m pytest
  tests/test_compiler.py::TestCompileShortDoc::test_rolls_back_summary_when_concept_planning_fails
  -q` first failed because a later planning exception left
  `wiki/summaries/test-doc.md` behind, then passed after compile staging was
  added.
- `.\.venv\Scripts\python.exe -m pytest tests/test_compiler.py -q` passed with
  `79 passed` after adding staged compile commits.
- `.\.venv\Scripts\python.exe -m pytest tests/test_add_command.py
  tests/test_client_server.py tests/test_client_jobs.py -q` passed with
  `26 passed` after confirming add/client callers still work with staged
  compile wrappers.
- `.\.venv\Scripts\python.exe -m pytest
  tests/test_add_command.py::TestRebuildCommand -q` first failed because
  `openkb rebuild` did not exist, then passed after adding the command.
- `.\.venv\Scripts\python.exe -m pytest tests/test_add_command.py -q` passed
  with `21 passed` after adding `openkb rebuild`.
- `.\.venv\Scripts\python.exe -m pytest
  tests/test_cli.py::test_init_creates_structure
  tests/test_cli.py::test_init_schema_content
  tests/test_list_status.py::TestListCommand::test_list_shows_expanded_investment_schema_pages
  tests/test_list_status.py::TestStatusCommand::test_status_shows_directory_counts
  tests/test_lint.py::TestCheckIndexSync::test_expanded_investment_schema_page_not_in_index
  tests/test_query.py::TestBuildQueryAgent::test_instructions_read_expanded_investment_schema_pages
  -q` first failed because the expanded investment schema was not wired into
  init/list/status/lint/query, then passed after schema support was added.
- `.\.venv\Scripts\python.exe -m pytest tests/test_cli.py
  tests/test_list_status.py tests/test_lint.py tests/test_query.py
  tests/test_client_kb.py -q` passed with `63 passed` after adding the optional
  expanded investment schema.
- `.\.venv\Scripts\python.exe -m pytest -q` passed with `311 passed` after all
  optimization roadmap items in this document were completed.
