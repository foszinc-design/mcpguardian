# MCPGuardian Production Bundle

This bundle includes Phase 1 through Phase 9 plus production rollout helpers.

Primary docs:

```text
PRODUCTION_ROLLOUT.md
SECURITY_BASELINE.md
RELEASE_CHECKLIST.md
README_PHASE1.md ... README_PHASE9.md
```

Primary operator CLI:

```powershell
C:\Python311\python.exe mcpguardianctl.py doctor --root . --config-path .\config\gateway_config.phase9.example.json
```

Core entrypoints:

```text
guardian_gateway.py       # Claude Desktop / Codex stdio
guardian_http_gateway.py  # ChatGPT remote MCP through HTTPS tunnel
```

The HTTP gateway refuses to start unless `MCPGUARDIAN_BEARER_TOKEN` is set.
