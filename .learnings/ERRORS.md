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
## [ERR-20260506-004] powershell_inline_python_unicode_path

**Logged**: 2026-05-06T15:45:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
Inline Python launched from PowerShell corrupted a Chinese filesystem path in source code, causing `Path.open()` to fail on `D:\知识库\...`.

### Error
```text
OSError: [Errno 22] Invalid argument: 'D:\\???\\llm-investment-kb\\.openkb\\config.yaml'
```

### Context
- Operation attempted: update `D:\知识库\llm-investment-kb\.openkb\config.yaml` from an inline Python script.
- The same path existed in PowerShell, but embedding it directly in the Python heredoc turned the Chinese directory segment into question marks.

### Suggested Fix
Resolve Unicode paths in PowerShell first, pass them via environment variables, and read them with `os.environ[...]` in Python.

### Metadata
- Reproducible: yes
- Related Files: D:\知识库\llm-investment-kb\.openkb\config.yaml

### Resolution
- **Resolved**: 2026-05-06T15:45:00+08:00
- **Notes**: Re-ran the script with `$env:KB_PATH=(Get-Item -LiteralPath ...).FullName` and `Path(os.environ["KB_PATH"])`.

---
## [ERR-20260506-006] pytest_global_python_missing_agents

**Logged**: 2026-05-06T17:10:00+08:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
Running `python -m pytest` from the global Python interpreter failed because project test dependencies such as `agents` are installed in the repository `.venv`.

### Error
```text
ModuleNotFoundError: No module named 'agents'
```

### Context
- Command attempted: `python -m pytest tests/test_agent_tools.py tests/test_query.py`
- Environment: PowerShell in `D:\workspace\codex\jt-ai-tz\OpenKB`

### Suggested Fix
Use `.\.venv\Scripts\python.exe -m pytest ...` for this repository's tests.

### Metadata
- Reproducible: yes
- Related Files: none

---
## [ERR-20260506-004] rg_access_denied_in_powershell

**Logged**: 2026-05-06T14:55:57+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
`rg` failed with Access denied in this PowerShell session, so code search had to fall back to `Get-ChildItem` plus `Select-String`.

### Error
```text
Program 'rg.exe' failed to run: Access is denied
```

### Context
- Command attempted: `rg -n "model_pool|llm_profiles|active_llm_profile|Enable Model|strategy" openkb tests`
- Environment: PowerShell in `D:\workspace\codex\jt-ai-tz\OpenKB`

### Suggested Fix
Use PowerShell `Select-String` as a fallback when `rg` is unavailable or blocked in this workspace.

### Metadata
- Reproducible: unknown
- Related Files: none

---
## [ERR-20260506-005] node_playwright_module_missing

**Logged**: 2026-05-06T09:06:00+08:00
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
Ad hoc Node browser verification failed because the repository does not have a local `playwright` npm module installed.

### Error
```text
Error: Cannot find module 'playwright'
```

### Context
- Operation attempted: `node -` script requiring `playwright` to verify Jobs log scroll behavior against static HTML.
- The Python test suite and static tests still run normally.

### Suggested Fix
Use the configured Playwright MCP when available, or run verification through `npx --package playwright` / an existing bundled browser dependency instead of assuming a local npm dependency.

### Metadata
- Reproducible: yes
- Related Files: openkb/client/static/app.js

---
## [ERR-20260506-004] playwright_mcp_browser_profile_in_use

**Logged**: 2026-05-06T09:05:00+08:00
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
Playwright MCP refused a browser code verification because the shared browser profile was already in use.

### Error
```text
Browser is already in use for C:\Users\yt_wa\AppData\Local\ms-playwright\mcp-chrome-b6f5f4d, use --isolated to run multiple instances of the same browser
```

### Context
- Operation attempted: DOM-level verification that the Jobs log panel preserves scroll position after `renderJobsPanel()`.
- The MCP tool schema exposed no `--isolated` option for this call.

### Suggested Fix
When the Playwright MCP browser profile is locked, use an independent Playwright process with a temporary user data directory, or close/restart the MCP browser before retrying.

### Metadata
- Reproducible: unknown
- Related Files: openkb/client/static/app.js

---
## [ERR-20260506-003] rg_access_denied_in_codex_desktop

**Logged**: 2026-05-06T08:34:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
`rg.exe` failed with access denied in this Codex desktop session, requiring a PowerShell-native fallback for repository search.

### Error
```text
Program 'rg.exe' failed to run: Access is denied
```

### Context
- Commands attempted: `rg -n "session|Session|sessions|Sessions" .` and `rg --files`
- Environment: Windows PowerShell in `D:\workspace\codex\jt-ai-tz\OpenKB`
- Fallback used: `Get-ChildItem -Recurse -File | Select-String ...`

### Suggested Fix
When `rg` is unavailable or blocked on this workspace, use PowerShell `Get-ChildItem` plus `Select-String`, excluding large generated directories explicitly.

### Metadata
- Reproducible: unknown
- Related Files: n/a

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

## [ERR-20260505-001] pageindex_official_requirements_conflict

**Logged**: 2026-05-05T18:25:00+08:00
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
The official `VectifyAI/PageIndex` `requirements.txt` currently pins `python-dotenv==1.2.2`, which conflicts with `litellm==1.83.7` because that LiteLLM version requires `python-dotenv==1.0.1`.

### Error
```text
ERROR: Cannot install -r C:\Users\yt_wa\pageindex-local\repo\requirements.txt (line 1) and python-dotenv==1.2.2 because these package versions have conflicting dependencies.
ERROR: ResolutionImpossible
```

### Context
- Command attempted: `venv\Scripts\python.exe -m pip install -r C:\Users\yt_wa\pageindex-local\repo\requirements.txt`
- Environment: Windows PowerShell, Python 3.12, local install root `C:\Users\yt_wa\pageindex-local`
- The repository clone was the current `main` head at commit `a51d97f63cedbf1d36b1121ff47386ea4e088ff5`.

### Suggested Fix
Install the runtime with the same pinned set except override `python-dotenv` to `1.0.1`, or update the upstream `requirements.txt` to a dependency set that resolves cleanly.

### Metadata
- Reproducible: yes
- Related Files: C:\Users\yt_wa\pageindex-local\repo\requirements.txt

---
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
- Recurrence-Count: 2
- Last-Seen: 2026-05-05T20:14:59+08:00

---

## [ERR-20260505-004] system_python_missing_agents_dependency

**Logged**: 2026-05-05T20:14:59+08:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
The system `python` interpreter could not import `agents`, so OpenKB tests had to run with the project virtual environment.

### Error
```text
ModuleNotFoundError: No module named 'agents'
```

### Context
- Command attempted: `python -m pytest tests/test_query.py tests/test_agent_tools.py -q`
- The repository already contains a `.venv` with the project dependencies installed.

### Suggested Fix
Use `.\\.venv\\Scripts\\python.exe -m pytest ...` for local verification inside this workspace.

### Metadata
- Reproducible: yes
- Related Files: tests/test_query.py, tests/test_agent_tools.py

---

## [ERR-20260505-005] openkb_query_no_windows_console

**Logged**: 2026-05-05T20:31:00+08:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
`openkb query` can start the query agent in the Codex PowerShell environment, but fails before completion because no Windows console is available.

### Error
```text
[ERROR] Query failed: No Windows console found. Are you running cmd.exe?
```

### Context
- Command attempted: `.venv\Scripts\python.exe -m openkb --kb-dir D:\知识库\openkb-test5 query "..."`
- The command printed the agent's first step, such as needing to read `index.md`, then failed.
- File-level inspection and `openkb status/list/lint/source` still work through the project virtual environment.

### Suggested Fix
Audit any console-specific prompt or rendering dependency in the query path and add a non-interactive fallback for hosted shells, CI, and Codex desktop sessions.

### Metadata
- Reproducible: yes
- Related Files: openkb/cli.py, openkb/agent/query.py
- See Also: ERR-20260505-004

---
## [ERR-20260506-001] cleanup_removed_tracked_playwright_artifact

**Logged**: 2026-05-06T07:12:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
While cleaning Playwright verification artifacts, a tracked `.playwright-mcp` log file was removed along with newly generated screenshots/snapshots.

### Error
```text
git status showed: D .playwright-mcp/console-2026-05-03T10-48-31-537Z.log
```

### Context
- Cleanup command removed the whole `.playwright-mcp` directory after browser verification.
- Some files in that directory were already tracked in this repository.

### Suggested Fix
Before recursive cleanup of tool artifact directories, check `git status --short <path>` or remove only the known newly generated filenames.

### Metadata
- Reproducible: yes
- Related Files: .playwright-mcp/console-2026-05-03T10-48-31-537Z.log

### Resolution
- **Resolved**: 2026-05-06T07:12:00+08:00
- **Notes**: Restored the tracked file with `git restore -- .playwright-mcp/console-2026-05-03T10-48-31-537Z.log`.

---
## [ERR-20260506-002] playwright_mcp_cache_disable_method

**Logged**: 2026-05-06T08:03:32+08:00
**Priority**: low
**Status**: resolved
**Area**: frontend

### Summary
The Playwright MCP page object in this session did not expose `page.setCacheEnabled`, so cache-busting had to be done by using a fresh port or updating asset URLs.

### Error
```text
TypeError: page.setCacheEnabled is not a function
```

### Context
- Operation attempted: browser visual verification after changing static CSS/JS.
- The page had previously loaded the old `/assets/app.js` and `/assets/styles.css`, so a hard reload/cache-bust was needed.

### Suggested Fix
For this MCP wrapper, avoid `page.setCacheEnabled`. Use a fresh local server port, a cache-busted navigation URL, or rewrite stylesheet/script asset URLs with `?v=<timestamp>` during verification.

### Metadata
- Reproducible: yes
- Related Files: openkb/client/static/app.js, openkb/client/static/styles.css

### Resolution
- **Resolved**: 2026-05-06T08:03:32+08:00
- **Notes**: Verified the updated UI on a fresh `8766` server and cache-busted the stylesheet URL before taking the final screenshot.

---
## [ERR-20260506-003] playwright_strict_role_selector_ambiguity

**Logged**: 2026-05-06T08:51:00+08:00
**Priority**: low
**Status**: resolved
**Area**: frontend

### Summary
Playwright strict role matching treated the Wiki editor `Source` tab and the left navigation `Sources` button as ambiguous when using a broad role selector.

### Error
```text
locator.click: Error: strict mode violation: getByRole('button', { name: 'Source' }) resolved to 2 elements
```

### Context
- Operation attempted: browser verification of the Wiki Preview/Source mode toggle.
- The navigation item `Sources` and editor tab `Source` both matched the non-exact role query.

### Suggested Fix
Use exact accessible names or stable data attributes for UI verification, for example `[data-action="wiki-mode"][data-wiki-mode="source"]`.

### Metadata
- Reproducible: yes
- Related Files: openkb/client/static/app.js

### Resolution
- **Resolved**: 2026-05-06T08:51:00+08:00
- **Notes**: Re-ran the verification with the explicit `data-action` selector.

---
