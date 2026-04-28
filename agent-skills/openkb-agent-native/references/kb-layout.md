# KB Layout

## Root Layout

OpenKB-compatible knowledge bases should keep this layout:

```text
<kb-root>/
  .openkb/
    config.yaml
    hashes.json
    chats/
  raw/
  wiki/
    AGENTS.md
    index.md
    log.md
    sources/
    summaries/
    concepts/
    explorations/
    reports/
```

## Compatibility Notes

- `.openkb/config.yaml` stores KB-level defaults and may include `agent_native: true`
- `.openkb/hashes.json` is the primary dedupe and document-state registry
- `raw/` stores source artifacts
- `wiki/sources/` stores converted document content
- `wiki/summaries/` stores one page per source document
- `wiki/concepts/` stores cross-document synthesis
- `wiki/explorations/` stores saved query outputs
- `wiki/reports/` stores lint output

## Packaging Note

This skill is self-contained relative to the `OpenKB/openkb/` source tree.

It still expects a Python environment with the document-conversion dependencies installed, especially:

- `PyYAML`
- `pymupdf`
- `markitdown`

## Current-Directory Rule

This skill assumes the current working directory is the KB root unless the user explicitly says otherwise.
