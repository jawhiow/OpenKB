## [LRN-20260506-001] correction

**Logged**: 2026-05-06T19:05:00+08:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
When the user says the repository should be bound to Git in an OpenKB context, confirm whether they mean the runtime knowledge base directory before changing the source repository.

### Details
The user requested Git binding, per-task commits, and ignoring `raw/` files. I first applied that to the OpenKB source repository, but the user clarified the requirement is for the knowledge base managed by OpenKB, not this code repository.

### Suggested Action
Treat Git binding for knowledge bases as OpenKB product behavior: runtime KB directories should initialize/configure Git, ignore user-provided `raw/` documents, and commit successful task output when files changed.

### Metadata
- Source: user_feedback
- Related Files: openkb/cli.py, openkb/agent/compiler.py
- Tags: git, knowledge-base, product-behavior

---
