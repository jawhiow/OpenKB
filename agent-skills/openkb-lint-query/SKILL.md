---
name: openkb-lint-query
description: Query, cite, lint, and safely repair an OpenKB runtime knowledge base from the knowledge base directory. Use when Codex is inside or near an OpenKB KB containing wiki/, .openkb/, or raw/ and the user asks to query the KB, ask questions over the wiki, compare, summarize, find evidence, save an exploration, run lint, inspect wiki health, fix broken links, create missing draft concept/company/industry pages, or improve query usability. Do not use for editing the OpenKB source code repository unless the user explicitly asks to change OpenKB itself.
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

Do not save answers by default. Save only when the user explicitly asks to save, persist, or create an exploration:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\save_exploration.py" --kb . --title "<title>" --answer "<answer-file>" --json
```

For more detail, read `references/query-playbook.md`.

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

Draft pages and TODO scaffolding are opt-in only:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --create-drafts --add-todos --json
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
