# MCPGuardian

Hard-gate enforcement layer for MCP (Model Context Protocol) tool operations.

## What is MCPGuardian?

MCPGuardian is an operational control system that sits between AI assistants (Claude Desktop, Codex CLI, ChatGPT) and backend MCP servers. It enforces policy gates, structured traces, and resilience controls on every tool call.

**MCPGuardian does not trust observations automatically.** All rules go through structured evidence collection, pending review, and explicit human approval before becoming active enforcement policies.

## Architecture

```
AI Client (Claude/Codex/ChatGPT)
  ↓ stdio or HTTP
MCPGuardian Gateway
  ├── Preflight Gate (allow/warn/block/require_artifact/postcheck_required)
  ├── Structured Trace (JSONL per run)
  ├── Resilience (circuit breaker, retry, auto-restart)
  └── Policy Rules (active_rules.json, pending_rules.json)
  ↓ stdio
Backend MCP Servers (Desktop Commander, Windows-MCP, Filesystem)
```

## Phases

| Phase | Description | Status |
|-------|------------|--------|
| 1 | Gate + Trace + Atomic I/O | Done |
| 2 | XLSX Validator | Done |
| 3 | Rule Analyzer + Reviewer | Done |
| 4 | Claim Manifest Validator | Done |
| 5A | FastMCP Control Plane | Done |
| 5B | Gateway (non-bypassable) | Done |
| 6 | Resilience (circuit breaker, retry, restart) | Done |
| 6.5 | HTTP Remote Adapter | Planned |
| 7 | Windows Event Guard | Planned |
| 8 | Operational Packaging | Planned |

## Key Principles

1. **No automatic rule activation** - all rules start as `proposed` in `pending_rules.json`
2. **Human approval required** - only `rule_reviewer approve` promotes to active
3. **Artifact existence ≠ gate pass** - artifact shape and validity are verified
4. **Metadata inspection ≠ analytical coverage** - `global_claim_safe` defaults to `false`
5. **Tool call retry disabled by default** - side effects can't be safely retried

## License

MIT
