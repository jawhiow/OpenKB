# OpenKB Agent Guide

## Project Purpose
OpenKB is a Karpathy-style AI private knowledge base builder. It compiles raw
documents into a persistent Markdown wiki with summaries, concept pages,
cross-links, saved explorations, lint reports, and chat history.

## Repository Map
- `openkb/`: Python package and command-line entry point.
- `openkb/agent/`: LLM agent workflows for compilation, query, chat, linting,
  and safe wiki tools.
- `agent-skills/`: Native agent skill assets and scripts for OpenKB workflows.
- `tests/`: Pytest coverage for CLI commands, conversion, state, agents, and
  supporting utilities.
- `docs/superpowers/`: Design notes and implementation plans for larger agentic
  changes.

## Runtime Knowledge Base Layout
Running `openkb init` creates a knowledge base directory with:
- `raw/`: user-provided source documents.
- `wiki/`: generated Markdown wiki.
- `wiki/AGENTS.md`: runtime wiki schema read by OpenKB agents.
- `.openkb/`: local state, config, hashes, and chat sessions.

The repository root `AGENTS.md` is for developers and coding agents. The runtime
`wiki/AGENTS.md` is for OpenKB's LLM behavior inside a specific knowledge base.

## Common Commands
```bash
pip install -e ".[dev]"
python -m pytest
openkb init
openkb add path/to/document.pdf
openkb query "What are the main findings?"
openkb chat
openkb lint
openkb status
```

When the local client extra is installed:
```bash
pip install -e ".[client]"
openkb client
```

## Development Rules
- Prefer existing OpenKB modules over duplicating behavior.
- Keep CLI printing separate from structured data helpers so the client and CLI
  can share the same underlying logic.
- Keep all wiki file access constrained to the active KB's `wiki/` directory.
- Never return `.env` contents or API keys from client APIs.
- Avoid real LLM calls in unit tests; mock query, add, and lint workflows.
- Do not delete or rewrite user documents unless the user explicitly asks.
- Do not commit generated knowledge base artifacts from `raw/`, `wiki/`, or
  `.openkb/`.

## Testing Expectations
- Add tests before new production behavior.
- Cover path traversal, missing KBs, job failure states, and config persistence.
- Run targeted tests for changed modules, then run the full test suite before
  claiming completion.

