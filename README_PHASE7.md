# MCPGuardian Phase 7: Native Tool Integration

Phase 7 moves the highest-use local tools into MCPGuardian itself. The goal is to reduce dependency on unstable external stdio MCP servers while preserving the existing Phase 5B/6 backend proxy path for gradual migration.

## What changed

Added native Python tools under `guardian/tools/`:

```text
guardian/tools/
  __init__.py
  common.py
  registry.py
  powershell.py
  file_ops.py
  process_mgr.py
  screenshot.py
  clipboard.py
```

`GatewayRouter` now routes tool calls in this order:

```text
1. internal gateway tools, for example mcpguardian_gateway_status
2. native guardian_* tools
3. proxied backend MCP tools from Phase 5B/6
```

The backend proxy path remains intact. Desktop Commander can stay during migration; Windows-MCP and Filesystem can be removed after native tools are validated in Claude Desktop.

## Native tools

### PowerShell / wait

```text
guardian_powershell
guardian_wait
```

`guardian_powershell` captures stdout/stderr, return code, duration, and supports timeout. On Windows it runs PowerShell/PowerShell Core. On non-Windows it uses `pwsh` when available and a shell fallback for test/dev compatibility.

Dangerous command patterns are blocked in the tool before execution, including examples such as `Format-Disk`, `Clear-Disk`, recursive `Remove-Item`, `rm -rf /`, registry mutation, and boot configuration commands.

### File operations

```text
guardian_read_file
guardian_read_multiple_files
guardian_write_file
guardian_edit_file
guardian_list_directory
guardian_directory_tree
guardian_move_file
guardian_create_directory
guardian_get_file_info
guardian_search_files
```

Writes and edits use atomic rewrite semantics through the existing `atomic_io` layer. `guardian_edit_file` uses exact string replacement and refuses ambiguous replacements unless `replace_all=true` or `expected_replacements` is provided.

Phase 7 intentionally does not implement Excel/DOCX/PDF parsing. Use the existing XLSX validator for workbook metadata, and keep rich document parsing for a later phase.

### Process management

```text
guardian_start_process
guardian_list_processes
guardian_kill_process
```

Absolute executable paths must be inside `allowed_roots`. Plain executable names such as `python`, `notepad`, or `pwsh` are allowed for PATH lookup. `kill_process` terminates the process tree by default and cleans up registered subprocess handles.

### Screenshot

```text
guardian_screenshot
```

Uses Pillow `ImageGrab` when available. This requires a real desktop session. Headless environments should expect `SCREENSHOT_FAILED` or `DEPENDENCY_MISSING`.

## Enforcement behavior

Every native tool call goes through the same hard-gate path as backend tools:

```text
GatewayRouter.call_tool
  -> path_policy argument scan
  -> RunContext + trace.jsonl
  -> preflight_gate with context:
       mcp_backend = native
       mcp_tool_name = guardian_<tool>
       command = <PowerShell command when present>
  -> native tool execution
  -> trace finish
```

Native tools can be governed by `active_rules.json` using existing gateway conditions:

```json
{
  "id": "native.block.write.v1",
  "status": "active",
  "scope": "gateway",
  "task_types": ["mcp_tool_call"],
  "severity": "critical",
  "enforcement": "block",
  "condition": {
    "mcp_backend": "native",
    "mcp_tool_name": "guardian_write_file"
  },
  "required_artifacts": [],
  "message": "Native file writes are blocked."
}
```

PowerShell command rules can also match command text:

```json
{
  "id": "native.block.shutdown.v1",
  "status": "active",
  "scope": "gateway",
  "task_types": ["mcp_tool_call"],
  "severity": "critical",
  "enforcement": "block",
  "condition": {
    "mcp_backend": "native",
    "mcp_tool_name": "guardian_powershell",
    "command_regex_any": ["shutdown", "Format-Disk", "Clear-Disk"]
  },
  "required_artifacts": [],
  "message": "Dangerous PowerShell command is blocked."
}
```

Unsupported condition keys still fail closed; they do not silently broaden rule scope.

## Claude Desktop migration

Target config after Phase 7 validation:

```json
{
  "mcpServers": {
    "mcp-guardian": {
      "type": "stdio",
      "command": "C:\\Python311\\python.exe",
      "args": ["F:\\Projects\\MCPGuardian\\guardian_gateway.py"],
      "env": {
        "MCPGUARDIAN_ROOT": "F:\\Projects\\MCPGuardian",
        "MCPGUARDIAN_CONFIG": "F:\\Projects\\MCPGuardian\\config\\gateway_config.json",
        "MCPGUARDIAN_ENABLE_RULE_MUTATION": "0"
      }
    }
  }
}
```

Recommended migration order:

1. Add Phase 7 and keep existing backend entries disabled or present behind Gateway only.
2. Validate `guardian_read_file`, `guardian_write_file`, `guardian_edit_file`, `guardian_list_directory`, and `guardian_powershell` from Claude Desktop.
3. Remove `windows-mcp` from Claude Desktop config.
4. Remove `filesystem` from Claude Desktop config.
5. Keep Desktop Commander only if a workflow still needs a feature not covered by native tools.
6. Move remaining Desktop Commander workflows behind Gateway proxy or implement native equivalents later.

Zero external backend example:

```json
{
  "schema_version": "1.0",
  "paths": {
    "runs_dir": "runs",
    "active_rules": "config/active_rules.json",
    "pending_rules": "config/pending_rules.json",
    "rule_history": "config/rule_history.jsonl"
  },
  "allowed_roots": ["F:\\Projects\\MCPGuardian", "F:\\ChemicalAI"],
  "gateway": {"tool_separator": "__", "native_tools": true},
  "backends": {}
}
```

## Security checklist

- Keep `allowed_roots` minimal.
- Do not allow whole-drive roots unless absolutely required.
- Keep `MCPGUARDIAN_ENABLE_RULE_MUTATION=0` by default.
- Add active rules for destructive native tools in high-risk directories.
- Block or warn on `guardian_kill_process`, `guardian_move_file`, `guardian_write_file`, and broad PowerShell commands if operating on sensitive paths.
- Keep trace logs enabled; every native tool call writes `trace.jsonl`.
- Do not expose HTTP mode without Phase 6.5 bearer auth and a tunnel/access layer.

## Tests

Phase 7 adds native tool tests:

```text
tests/test_powershell_tool.py
tests/test_file_ops_tool.py
tests/test_process_mgr_tool.py
tests/test_screenshot_tool.py
```

Current result:

```text
Ran 67 tests in 26.503s

OK
```

## Deliberately not included

- Excel/DOCX/PDF parsing implementation
- Registry mutation tools
- Full UI automation Click/Type/Move
- Removal of backend proxy code
- Automatic Claude Desktop config editing
