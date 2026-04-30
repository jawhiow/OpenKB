## [ERR-20260430-001] powershell_heredoc

**Logged**: 2026-04-30T12:45:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Attempted Unix-style `python - <<'PY'` in PowerShell, which fails because PowerShell treats `<` as a reserved redirection operator.

### Error
```text
Missing file specification after redirection operator.
The '<' operator is reserved for future use.
```

### Context
- Command attempted while inspecting Python signatures from a PowerShell shell.
- Environment shell is PowerShell on Windows.

### Suggested Fix
Use PowerShell here-strings piped into Python, e.g. `@'... '@ | .\.venv\Scripts\python.exe -`, or `python -c "..."` for short snippets.

### Metadata
- Reproducible: yes
- Related Files: none

---

## [ERR-20260430-002] node_repl_runtime_version

**Logged**: 2026-04-30T13:10:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Attempted to use the node_repl MCP to start a local fake LLM server, but the configured Node.js runtime was below the MCP server's minimum version.

### Error
```text
Node runtime too old for node_repl (resolved C:\Program Files\nodejs\node.exe): found v22.19.0, requires >= v22.22.0.
```

### Context
- Operation attempted while preparing browser E2E verification for OpenKB.
- Environment is Windows PowerShell with Node.js v22.19.0 on PATH.

### Suggested Fix
Use a newer Node.js runtime for node_repl, set NODE_REPL_NODE_PATH to a compatible binary, or use a PowerShell/Python background process for local test servers.

### Metadata
- Reproducible: yes
- Related Files: none

---
## [ERR-20260430-003] powershell_colon_interpolation

**Logged**: 2026-04-30T13:03:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
PowerShell parsed `$pidValue:` inside a double-quoted string as an invalid variable reference while cleaning up temporary E2E processes.

### Error
```text
Variable reference is not valid. ':' was not followed by a valid variable name character.
```

### Context
- Command attempted while stopping local test servers on ports 8766 and 9876.
- The string included `"failed to stop $pidValue: ..."`.

### Suggested Fix
Use `${pidValue}` when a variable is immediately followed by punctuation in a double-quoted PowerShell string.

### Metadata
- Reproducible: yes
- Related Files: none

---
## [ERR-20260430-004] powershell_nested_redaction_command

**Logged**: 2026-04-30T13:35:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
A nested one-line PowerShell command for redacting config and env files failed because the braces became hard to audit.

### Error
```text
Unexpected token '}' in expression or statement.
```

### Context
- Command attempted while inspecting OpenKB runtime configuration without exposing secrets.
- The script mixed nested loops, if/else blocks, regex replacements, and redaction logic in one long command.

### Suggested Fix
Use smaller PowerShell commands or a here-string script for multi-branch redaction logic; keep secret-redaction inspection readable.

### Metadata
- Reproducible: yes
- Related Files: none

---
