# Lint Playbook

Use this when checking or repairing an OpenKB runtime wiki.

## Lint Layers

- Structural: broken wikilinks, orphan pages, missing raw/source/summary links, index drift, frontmatter source drift, evidence map drift, path traversal.
- Semantic: near-duplicate concepts, company pages misplaced under concepts, generic titles, pages without evidence sections, stale or weakly supported claims.
- Query usability: missing briefs, missing Source Evidence sections, long-doc summaries without page-level access, pages not discoverable from `index.md`.
- Compounding: repeated queries with no saved explorations, repeated linked topics that never become durable pages.

## Safe Auto-Fixes

Allowed by default:

- Add missing pages to `index.md`.
- Rewrite obvious wikilinks when the target is a clear case/stem/path match.
- Write Markdown and JSON reports under `wiki/reports/`.

Opt-in only:

- Create draft pages in `concepts/`, `companies/`, `industries/`, or `explorations/` with `--create-drafts`.
- Append `Source Evidence` TODO sections to important pages with `--add-todos`.
- Create a company draft and add a review note when a concept page clearly looks company-specific with `--create-drafts`.

Manual review only:

- Delete pages.
- Merge pages.
- Rewrite claims that may be disputed.
- Resolve contradictions without source evidence.
- Overwrite `wiki/sources/`.
- Modify `raw/`.
- Expose secrets.

## Recommended Commands

Safe auto-fix:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --json
```

Conservative semantic report without content changes:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --report-only --json
```

Opt in to scaffolding only after reviewing the report:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --create-drafts --add-todos --json
```

Report-only:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\lint_kb.py" --kb . --report-only --json
```

Approved plan:

```bash
python "%USERPROFILE%\.codex\skills\openkb-lint-query\scripts\apply_fixes.py" --kb . --plan "wiki/reports/lint_YYYYMMDD_HHMMSS.json" --json
```

After lint, summarize:

- report path
- issue count by severity
- files changed
- manual-review items
- any git status before/after when available
