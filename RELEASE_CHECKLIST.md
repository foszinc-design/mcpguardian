# MCPGuardian Release Checklist

## Build

- [ ] `python -m unittest` targeted suites pass.
- [ ] `python mcpguardianctl.py release-manifest --root .` returns `ok: true`.
- [ ] No `__pycache__` or `.pyc` files included in release archive.
- [ ] `requirements.txt` reviewed.

## Local stdio

- [ ] `guardian_gateway.py` starts under Claude Desktop.
- [ ] Native tools appear in `tools/list`.
- [ ] External backends can be disabled when native replacement is sufficient.

## HTTP

- [ ] `MCPGUARDIAN_BEARER_TOKEN` is 32+ random chars.
- [ ] `/health` returns 200 without auth.
- [ ] `/mcp` rejects missing/invalid token with 401.
- [ ] Cloudflare Tunnel maps HTTPS hostname to `127.0.0.1:8000`.

## Safety

- [ ] `allowed_roots` minimized.
- [ ] Hardened rules enabled or equivalent active rules present.
- [ ] Write/delete/move operations blocked unless specifically approved.
- [ ] Rule mutation disabled.
- [ ] Rollback backup verified.

## Rollback

- [ ] Claude Desktop config backup exists.
- [ ] `mcpguardianctl.py rollback-claude` tested against a copy.
- [ ] Previous external MCP server config can be restored.
