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
## [ERR-20260503-001] rg_access_denied

**Logged**: 2026-05-03T13:42:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
`rg.exe` failed with Access is denied in this PowerShell workspace while searching the OpenKB repo.

### Error
```text
Program 'rg.exe' failed to run: Access is denied
```

### Context
- Command attempted: `rg -n "extract_coverage_gap|Coverage Gap|Cloud_CAPEX|_extract_concept_candidates_from_summary|apply_coverage_gap" tests openkb`
- Environment: Windows PowerShell in `D:\workspace\codex\jt-ai-tz\OpenKB`
- The fallback is to use `Select-String` or direct targeted file reads.

### Suggested Fix
Use PowerShell `Select-String` when `rg.exe` is blocked, or inspect PATH/permissions for the installed ripgrep executable.

### Metadata
- Reproducible: unknown
- Related Files: none

---
## [ERR-20260503-006] rg_access_denied_on_windows

**Logged**: 2026-05-03T09:20:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Running `rg.exe` from PowerShell in this OpenKB workspace failed with `Access is denied`, so repository text search had to fall back to PowerShell-native commands.

### Error
```text
Program 'rg.exe' failed to run: Access is denied
```

### Context
- Command attempted while searching OpenKB source files for investment optimization code paths.
- Environment: Windows PowerShell, workspace `D:\workspace\codex\jt-ai-tz\OpenKB`.

### Suggested Fix
Use `Select-String` / `Get-ChildItem` fallback when `rg.exe` is blocked, and investigate the local `rg.exe` path or endpoint security policy separately if this recurs.

### Metadata
- Reproducible: unknown
- Related Files: none

---
## [ERR-20260502-001] rg_access_denied

**Logged**: 2026-05-02T00:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Running `rg` in the OpenKB workspace failed with Windows `Access is denied`, so repository search needed a PowerShell fallback.

### Error
```text
Program 'rg.exe' failed to run: Access is denied
```

### Context
- Commands attempted: `rg -n "PageIndex|page_index|sources/|source_json|local_long|long_doc|compile_local" openkb tests` and `rg --files openkb tests`.
- Environment: Windows PowerShell in `D:\workspace\codex\jt-ai-tz\OpenKB`.

### Suggested Fix
Use `Get-ChildItem` plus `Select-String` as a fallback when `rg.exe` is blocked by the local environment.

### Metadata
- Reproducible: unknown
- Related Files: none
- Recurrence-Count: 2
- Last-Seen: 2026-05-03

---
## [ERR-20260502-001] rg_unicode_path_access_denied

**Logged**: 2026-05-02T20:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Running `rg --files` directly against a Windows path containing Chinese characters failed with `Access is denied`.

### Error
```text
Program 'rg.exe' failed to run: Access is denied
```

### Context
- Command attempted while enumerating `D:\知识库\openkb-test`.
- PowerShell native `Get-ChildItem -Recurse` remained available as a fallback.

### Suggested Fix
Use PowerShell native enumeration for non-ASCII Windows paths when `rg` launch fails, or run `rg` from inside the target directory if allowed.

### Metadata
- Reproducible: unknown
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
## [ERR-20260504-001] pytest_wrong_nodeid

**Logged**: 2026-05-04T21:33:00+08:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
Ran pytest with a stale/nonexistent node id while verifying client config behavior.

### Error
```text
ERROR: not found: D:\workspace\codex\jt-ai-tz\OpenKB\tests\test_client_server.py::test_test_llm_endpoint_uses_payload_values_and_masks_key
(no match in any of [<Module test_client_server.py>])
```

### Context
- Command attempted after adding a Settings save button in the browser client.
- The actual test name is `test_test_llm_endpoint_uses_current_form_values`.

### Suggested Fix
Search test names before targeting a specific node id, or run the whole relevant file when test naming has changed.

### Metadata
- Reproducible: yes
- Related Files: tests/test_client_server.py

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
## [ERR-20260430-005] powershell_python_stdin_unicode_path

**Logged**: 2026-04-30T13:42:00+08:00
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
Embedding a Chinese Windows path directly inside Python source piped through PowerShell stdin produced mojibake/question marks, causing diagnostics to inspect the wrong path.

### Error
```text
D:\知识库\investment-kb became D:\???\investment-kb in Python diagnostic output.
```

### Context
- Command attempted while diagnosing OpenKB config loading for a KB path with Chinese characters.
- The bad diagnostic reported default config and missing API key because it looked at a non-existent path.

### Suggested Fix
Pass non-ASCII paths through environment variables or command-line arguments, and make Python print/read using UTF-8 or Windows Unicode APIs. Avoid embedding non-ASCII paths in PowerShell-piped Python source.

### Metadata
- Reproducible: yes
- Related Files: none

---
## [ERR-20260505-001] powershell_select_string_multi_path

**Logged**: 2026-05-05T08:25:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
PowerShell `Select-String -Path` failed when multiple file paths were passed as positional arguments instead of a comma-separated array.

### Error
```text
Select-String : A positional parameter cannot be found that accepts argument 'tests\test_converter.py'.
```

### Context
- Command attempted while scanning several test files for changed call assertions.
- PowerShell treated the second path as an unexpected positional argument.

### Suggested Fix
Pass multiple paths as a comma-separated value to `-Path`, for example `Select-String -Path file1,file2 -Pattern ...`.

### Metadata
- Reproducible: yes
- Related Files: tests/test_add_command.py, tests/test_converter.py, tests/test_client_server.py, tests/test_client_ocr_api.py

---

## [ERR-20260505-002] powershell_complex_pattern_quoting

**Logged**: 2026-05-05T08:28:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
A complex `Select-String` pattern containing mixed quotes and backslashes failed PowerShell parsing before the search ran.

### Error
```text
The string is missing the terminator: ".
```

### Context
- Command attempted while searching tests for mock call sites.
- The regex mixed escaped double quotes, parentheses, and file patterns inside a JSON command string sent to PowerShell.

### Suggested Fix
Keep PowerShell search patterns simple, use single quoted patterns when possible, or split searches into smaller commands.

### Metadata
- Reproducible: yes
- Related Files: tests/test_converter.py, tests/test_add_command.py

---

## [ERR-20260505-003] rg_access_denied

**Logged**: 2026-05-05T09:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
`rg.exe` failed with Access denied in this PowerShell environment, blocking the preferred fast repository scan.

### Error
```text
Program 'rg.exe' failed to run: Access is denied
```

### Context
- Commands attempted: `rg --files` and a repository-wide `rg -n` TODO/search pattern.
- The repository is otherwise readable, so native PowerShell file enumeration and `Select-String` can be used as a fallback.

### Suggested Fix
Use PowerShell `Get-ChildItem` and `Select-String` when `rg.exe` is unavailable or blocked. If this recurs, inspect the `rg.exe` path and Windows execution restrictions.

### Metadata
- Reproducible: yes
- Related Files: none

---
