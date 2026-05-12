# MCPGuardian Phase 6 — Resilience Supervisor

Phase 6 adds backend resilience to the Phase 5B Gateway topology. The security topology does not change: Claude Desktop should expose only `mcp-guardian`, while backend MCP servers live behind Guardian Gateway.

## Implemented

- Per-backend circuit breaker
- Conservative retry policy with exponential backoff
- Backend restart on transport failure or timeout
- Best-effort process-tree cleanup via `psutil` when available
- Backend health and pressure metrics
- Gateway status tool: `mcpguardian_gateway_status`
- Trace events for gateway calls and circuit-open outcomes
- Configurable resilience settings per backend

## Safety decision: tool-call retry is off by default

`tools/call` retry is dangerous when the backend may have performed a side effect before the timeout. For example, `write_file` may have completed even if the Gateway timed out waiting for the response. Retrying blindly can duplicate writes or destructive operations.

Therefore Phase 6 only retries tool calls when both conditions are true:

```json
{
  "resilience": {
    "retry_tool_calls": true,
    "safe_retry_tools": ["read_*", "search", "echo"]
  }
}
```

Initialization and `tools/list` are retryable because they are control-plane operations.

## Example backend config

```json
{
  "backends": {
    "filesystem": {
      "command": "node",
      "args": ["C:/path/to/filesystem/server.js"],
      "tool_prefix": "fs",
      "timeout_seconds": 30,
      "resilience": {
        "max_retries": 1,
        "retry_delay_base_seconds": 0.25,
        "retry_delay_max_seconds": 3.0,
        "retry_backoff_multiplier": 2.0,
        "retry_tool_calls": false,
        "safe_retry_tools": [],
        "restart_on_failure": true,
        "restart_on_timeout": true,
        "kill_process_tree": true,
        "circuit_breaker_failure_threshold": 3,
        "circuit_breaker_recovery_seconds": 30
      }
    }
  }
}
```

## Gateway status tool

The gateway exposes:

```text
mcpguardian_gateway_status
```

It returns backend health, process state, circuit breaker state, retry counts, restart counts, stdout/stderr drain metrics, and pending high-watermark.

Example payload shape:

```json
{
  "ok": true,
  "backends": {
    "filesystem": {
      "running": true,
      "pid": 12345,
      "initialized": true,
      "tool_count": 12,
      "circuit_breaker": {
        "state": "closed",
        "consecutive_failures": 0
      },
      "metrics": {
        "starts": 1,
        "restarts": 0,
        "requests_total": 8,
        "requests_ok": 8,
        "timeouts": 0,
        "retries": 0,
        "pending_high_watermark": 1
      }
    }
  }
}
```

## What Phase 6 deliberately does not implement

- PnP event detection
- WMI storm debounce
- USB insertion pause/resume
- Drive-letter stabilization
- Windows notification hooks

Those belong to Phase 7. Phase 6 is transport/process resilience, not Windows event mitigation.

## Test result

```text
Ran 44 tests in 22.324s

OK
```
