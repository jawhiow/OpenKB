# OpenKB Runtime Contract

Use this contract when operating on an OpenKB knowledge base from a runtime directory.

## Directory Roles

- `raw/`: user-owned source documents. Do not edit, delete, rename, or rewrite files here unless the user explicitly asks.
- `wiki/sources/`: converted source evidence, including Markdown, JSON page stores, and image references. Treat as evidence, not synthesis. Do not overwrite from lint/query workflows.
- `wiki/summaries/`: one page per source document. Prefer these for document-level context before reading source pages.
- `wiki/companies/`: company-specific investment pages. Use for ratings, target prices, valuation context, company catalysts, risks, and exposure chains.
- `wiki/industries/`: industry, sector, value-chain, capacity-cycle, and competitive-map pages.
- `wiki/concepts/`: durable cross-document concepts, themes, risks, metrics, mechanisms, monitoring signals, and frameworks.
- `wiki/explorations/`: saved query results and analyses that are worth keeping.
- `wiki/reports/`: generated lint reports.

## Authority Order

1. User's current instruction.
2. `wiki/AGENTS.md` in the target KB.
3. Existing page conventions in that KB.
4. This skill's generic rules.

## Write Rules

- Update `wiki/index.md` when creating a page in `summaries/`, `companies/`, `industries/`, `concepts/`, or `explorations/`.
- Append `wiki/log.md` entries for lint and saved explorations.
- Prefer draft pages with `status: draft` for inferred missing knowledge.
- Keep manual review for deletions, merges, claim rewrites, contradiction resolution, and any operation with weak evidence.
- Never print `.env` contents or API key values. It is acceptable to report that an environment key exists or is missing.

## Citation Rules

- Cite durable wiki pages for synthesized claims.
- Cite summaries or source page ranges for source-specific claims.
- Use `wiki/evidence_map.json` when present to recover exact page references.
- If evidence is missing, say that the wiki lacks evidence instead of filling gaps from memory.
