# MCPGuardian Phase 5B — MCP Tool Gateway

Phase 5B is the first real enforcement-boundary integration.

Phase 5A exposed MCPGuardian as a control-plane MCP server. Phase 5B changes the topology: backend MCP servers must be removed from Claude Desktop config and launched only behind Guardian Gateway.

```text
Claude Desktop
  -> mcp-guardian gateway over stdio
      -> backend MCP server A over stdio
      -> backend MCP server B over stdio
      -> backend MCP server C over stdio
```

## Security boundary

This mode is only a hard enforcement boundary if Claude Desktop has no direct access to the backend MCP servers.

Remove direct entries such as:

```text
desktop-commander
windows-mcp
filesystem
```

Keep only:

```text
mcp-guardian
```

If backend servers remain directly registered in Claude Desktop, Guardian can still be bypassed.

## New files

```text
guardian_gateway.py                 # root entrypoint for Claude Desktop
guardian/gateway_server.py          # JSON-RPC stdio MCP server
guardian/gateway_router.py          # tool aggregation, routing, policy hook
guardian/backend_client.py          # async stdio MCP backend client
guardian/gateway_protocol.py        # JSON-RPC line framing helpers
config/gateway_config.example.json  # backend config template
tests/fake_mcp_backend.py           # test backend MCP server
tests/test_gateway_backend.py
tests/test_gateway_router.py
tests/test_gateway_server.py
```

## Implemented capabilities

- Launch backend MCP servers as child processes.
- Initialize backends via MCP JSON-RPC.
- Aggregate backend `tools/list` results.
- Namespace public tool names with backend prefixes.
- Route `tools/call` to the correct backend.
- Evaluate MCPGuardian active rules before forwarding tool calls.
- Block calls on `block` or `require_artifact` gate decisions.
- Deny path-like tool arguments outside configured `allowed_roots`.
- Write one run directory and trace per gateway tool call.
- Drain backend stdout and stderr concurrently.
- Return MCP-style tool errors without crashing the gateway.

## Deliberate non-goals

The following are still out of scope and belong to Phase 6 or later:

- circuit breaker
- automatic backend restart
- retry with exponential backoff
- stdout buffer pressure metrics
- process tree cleanup on Windows
- PnP/WMI event handling
- HTTP/SSE backend support
- Claude Desktop config migration automation

## Claude Desktop config

Use `guardian_gateway.py`, not `guardian/mcp_server.py`, for gateway mode.

```json
{
  "mcpServers": {
    "mcp-guardian": {
      "type": "stdio",
      "command": "C:\\Python311\\python.exe",
      "args": [
        "F:\\Projects\\MCPGuardian\\guardian_gateway.py"
      ],
      "env": {
        "MCPGUARDIAN_ROOT": "F:\\Projects\\MCPGuardian",
        "MCPGUARDIAN_CONFIG": "F:\\Projects\\MCPGuardian\\config\\gateway_config.json",
        "MCPGUARDIAN_ENABLE_RULE_MUTATION": "0"
      }
    }
  }
}
```

## Gateway config

Start from `config/gateway_config.example.json`.

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
  "allowed_roots": ["."],
  "gateway": {
    "tool_separator": "__"
  },
  "backends": {
    "filesystem": {
      "command": "node",
      "args": ["C:/path/to/filesystem/server.js"],
      "tool_prefix": "fs",
      "timeout_seconds": 30,
      "disabled": false
    }
  }
}
```

Backend tool `read_file` under prefix `fs` becomes public tool:

```text
fs__read_file
```

## Gateway-specific active rules

Phase 5B extends active rule conditions with:

```text
mcp_backend
mcp_backend_any
mcp_tool_name
mcp_tool_name_any
```

Example block rule:

```json
{
  "id": "gateway.block.filesystem.write.v1",
  "status": "active",
  "scope": "gateway",
  "task_types": ["mcp_tool_call"],
  "severity": "critical",
  "enforcement": "block",
  "condition": {
    "mcp_backend": "filesystem",
    "mcp_tool_name_any": ["write_file", "delete_file"]
  },
  "required_artifacts": [],
  "message": "Direct filesystem mutation is blocked by MCPGuardian Gateway."
}
```

Unsupported condition keys still fail closed. They do not silently broaden rule scope.

## Testing

```bash
python -m unittest discover -s tests -v
```

Expected result in this package:

```text
Ran 37 tests
OK
```

## Operational caveat

This gateway implements newline-delimited JSON-RPC stdio, which is the common local MCP server transport pattern. It does not implement Content-Length framing or SSE. If a backend uses a non-stdio transport, put an adapter in front of it or defer to a later transport expansion phase.
