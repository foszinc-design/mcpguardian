# MCPGuardian Phase 5A — FastMCP Control Plane Adapter

## Scope

Phase 5A exposes existing MCPGuardian v1.1 capabilities as MCP tools for Claude Desktop. It does **not** proxy backend MCP servers and does **not** enforce policy over `desktop-commander`, `windows-mcp`, `filesystem`, or any other MCP server that remains directly configured in Claude Desktop.

Security boundary:

> This MCP server exposes MCPGuardian controls to Claude Desktop. It does not enforce policy over other MCP servers unless those servers are removed from Claude Desktop config or routed through a future Guardian Gateway.

## New files

```text
guardian/
  mcp_server.py       # FastMCP stdio server entrypoint
  mcp_tools.py        # transport-agnostic MCP tool business logic
  path_policy.py      # allowed-root filesystem policy

config/
  mcp_guardian_config.json

tests/
  test_mcp_tools.py
```

## Exposed tools

```text
mcpguardian_preflight
mcpguardian_validate_xlsx
mcpguardian_validate_claim_manifest
mcpguardian_analyze_runs
mcpguardian_list_pending_rules
mcpguardian_approve_rule
mcpguardian_reject_rule
mcpguardian_get_run_summary
```

All tools return a stable JSON-safe envelope.

Success:

```json
{
  "ok": true,
  "message": "preflight evaluated",
  "run_id": "20260512_153012_ab12cd",
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

Failure:

```json
{
  "ok": false,
  "error_code": "VALIDATION_FAILED",
  "message": "claim manifest validation failed",
  "run_id": "20260512_153012_ab12cd",
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

## Rule mutation guard

`approve_rule` and `reject_rule` are intentionally disabled by default.

Both conditions must be true before rule mutation is allowed:

```text
MCPGUARDIAN_ENABLE_RULE_MUTATION=1
config/mcp_guardian_config.json: enable_rule_mutation=true
```

This preserves the v1.1 principle that observations must not become active enforcement rules without explicit human authorization.

## Claude Desktop config example

```json
{
  "mcpServers": {
    "mcp-guardian": {
      "type": "stdio",
      "command": "C:\\Python311\\python.exe",
      "args": [
        "F:\\Projects\\MCPGuardian\\guardian\\mcp_server.py"
      ],
      "env": {
        "MCPGUARDIAN_ROOT": "F:\\Projects\\MCPGuardian",
        "MCPGUARDIAN_CONFIG": "F:\\Projects\\MCPGuardian\\config\\mcp_guardian_config.json",
        "MCPGUARDIAN_ENABLE_RULE_MUTATION": "0"
      }
    }
  }
}
```

If you prefer module execution, set the working directory to the project root and use:

```text
command: C:\Python311\python.exe
args: ["-m", "guardian.mcp_server"]
```

## Configuration

`config/mcp_guardian_config.json`:

```json
{
  "schema_version": "1.0",
  "enable_rule_mutation": false,
  "paths": {
    "runs_dir": "runs",
    "active_rules": "config/active_rules.json",
    "pending_rules": "config/pending_rules.json",
    "rule_history": "config/rule_history.jsonl"
  },
  "allowed_roots": ["."]
}
```

`allowed_roots` is enforced by `path_policy.py`. Tool arguments that reference paths outside the configured roots are rejected with `PATH_POLICY_DENIED`.

## Validation status

```text
Ran 30 tests in 0.260s

OK
```

## Explicit non-goals

Phase 5A does not implement:

```text
backend MCP aggregation
backend tool proxying
stdio buffer monitoring
backend process restart
circuit breaker
PnP/WMI event handling
Claude Desktop config migration
```

Those belong to Phase 5B and later.
