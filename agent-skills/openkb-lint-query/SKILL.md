---
name: openkb-lint-query
description: Query, cite, lint, safely repair, add documents to, and delete indexed source documents from an OpenKB runtime knowledge base. Use when Codex is inside or near an OpenKB KB containing wiki/, .openkb/, or raw/ and the user asks to query the KB, ask questions over the wiki, compare, summarize, find evidence, save an exploration, run lint, inspect wiki health, fix broken links, create missing draft concept/company/industry pages, add/new/import files or documents, delete/remove a source document, or improve query usability. Do not use for editing the OpenKB source code repository unless the user explicitly asks to change OpenKB itself.
---

# OpenKB Lint Query

Use this skill in a live OpenKB knowledge base, not as a default codebase refactor workflow. Treat `wiki/AGENTS.md` as the runtime schema and prefer the KB's existing conventions over these generic instructions.

## First Step

Detect the KB before answering or fixing:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\detect_kb.py" --cwd . --json
```

If no KB is found, say so and ask the user to open the knowledge base root or a directory inside it. Never inspect or print `.env` contents.

## Query Workflow

Use this when the user asks to query or ask the KB, find evidence, compare pages, summarize themes, inspect a company/concept, or perform a similar knowledge-base question.

1. Build a context pack:

   ```bash
   python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\query_context.py" --kb . --question "<question>" --json
   ```

2. Read only the suggested `read_set_suggestion` files first. Expand to source pages only when the context pack says evidence is insufficient.
3. Answer in the KB language from `.openkb/config.yaml`, or in the user's language when no language is configured.
4. Cite every substantive claim with wiki references such as `[[concepts/x]]`, `[[summaries/y]] p.7`, or `sources/y.json pages 7-8`.
5. Separate supported conclusions from gaps: say what the wiki supports, what is missing, and what source/page should be checked next.
6. End with a short `Read set` listing the wiki pages and page ranges actually used.

For investment-decision questions such as "can I buy/invest", "is valuation reasonable", or "是否值得投资", use the context pack's `investment_decision` contract. Always distinguish business quality from the current buy price, read the suggested method pages, and state that current price/PE/FCF yield or equivalent valuation data is required before making a buy/sell conclusion.

Do not save answers by default. Save only when the user explicitly asks to save, persist, or create an exploration:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\save_exploration.py" --kb . --title "<title>" --answer "<answer-file>" --json
```

For more detail, read `references/query-playbook.md`.

## Add Document Workflow

Use this when the user asks to add, 新增, import, ingest, or compile files into the active KB. This operates on user-provided source files and uses OpenKB's normal conversion/compilation path.

Preview/detect the KB first, then add a file or directory:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\add_documents.py" --kb . --path "path/to/file-or-folder" --json
```

Force recompilation only when the user asks to re-add/rebuild/overwrite an already indexed document:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\add_documents.py" --kb . --path "path/to/file-or-folder" --force --json
```

The script skips unsupported extensions in directories and reports them in JSON. For a single unsupported file it returns an error. Adding may call the configured LLM/indexing pipeline, so report any conversion or compilation failures instead of inventing wiki pages manually.

## Delete Source Workflow

Use this when the user asks to delete, 删除, remove, or purge an indexed source document. Prefer deleting by source document selector (hash, hash prefix, file name, or stem), not by manually deleting generated wiki pages.

Start with dry-run preview, which is the default:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\delete_source.py" --kb . --selector "document-name-or-hash" --json
```

Only perform deletion after the user has clearly confirmed the exact source document:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\delete_source.py" --kb . --selector "document-name-or-hash" --yes --json
```

Deletion removes generated pages that belong only to that source, updates shared generated pages, removes matching raw/source/image artifacts through OpenKB's safe source-relations logic, and updates `.openkb/hashes.json`. Never delete arbitrary `raw/`, `wiki/sources/`, or generated wiki files by hand for source removal.

## Lint Workflow

Use this when the user asks for lint, health checks, wiki repair, broken-link cleanup, missing concepts, evidence gaps, or query-usability improvement.

Default to safe auto-fix mode:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --json
```

Use report-only mode when the user says not to change files:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --report-only --json
```

Default safe auto-fixes are conservative: they may update `index.md` and resolve obvious wikilink targets. Semantic findings such as duplicate concepts, company/concept boundary problems, missing evidence, and missing pages are reported as manual review by default.

Draft-page creation is opt-in only:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --create-drafts --json
```

Even with opt-in flags, lint must not delete pages, merge pages, rewrite disputed claims, overwrite `sources/`, modify `raw/`, or reveal secrets.

When a lint JSON report contains approved fix items, apply them with:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\apply_fixes.py" --kb . --plan "wiki/reports/lint_YYYYMMDD_HHMMSS.json" --json
```

For more detail, read `references/lint-playbook.md`.

## Runtime Contract

The KB is a compiled wiki:

- `raw/` is the immutable user-document layer.
- `wiki/sources/` is converted source evidence and should not be overwritten by this skill.
- `wiki/summaries/`, `wiki/companies/`, `wiki/industries/`, `wiki/concepts/`, and `wiki/explorations/` are the query and synthesis layer.
- `wiki/reports/` stores lint reports.
- `wiki/index.md` and `wiki/log.md` must stay in sync when this skill writes.
- `wiki/evidence_map.json`, when present, is preferred for citation grounding.

Read `references/openkb-runtime-contract.md` before unusual writes, migrations, or recovery work.
