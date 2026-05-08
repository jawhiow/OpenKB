# Query Playbook

Use this when answering questions from an OpenKB runtime wiki.

## Query Types

- Fact lookup: read `index.md`, then the most relevant page. Use evidence only when a claim needs exact support.
- Entity/company: start with `companies/`; then read related summaries, concepts, and industries.
- Concept/theme: start with `concepts/`; then pull summaries and contra-evidence.
- Global synthesis: group search results by summaries, companies, industries, concepts, and explorations; read representative pages per group before synthesizing.
- Deep dive/DRIFT: break a broad question into 3-5 subquestions, search each one, then merge findings.
- Figure/table: find image paths or source JSON page ranges; inspect images when needed instead of guessing from captions.

## Process

1. Run `query_context.py --kb . --question "<question>" --json`.
2. Read `index.md` and the candidate pages from `read_set_suggestion`.
3. If a candidate summary has `doc_type: pageindex` or `doc_type: local-long`, use the context pack's long-document hints. Read tight page ranges only.
4. Use `evidence_map.json` when exact support is needed.
5. Answer with citations on each substantive claim.
6. Include a final `Read set`.

## Answer Shape

Use a compact structure:

- Direct answer first.
- Evidence-backed bullets or paragraphs.
- "What the wiki does not establish" when relevant.
- `Read set`.

Do not save by default. Save only when the user explicitly requests persistence.
