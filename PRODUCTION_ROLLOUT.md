# MCPGuardian Production Rollout Guide

This guide is the operational cutoff after Phase 9. The goal is not more features; it is a safe switch from multiple local MCP servers to one hardened MCPGuardian entrypoint.

## 1. Preflight

Run diagnostics first.

```powershell
C:\Python311\python.exe mcpguardianctl.py doctor `
  --root "F:\Projects\MCPGuardian" `
  --config-path "F:\Projects\MCPGuardian\config\gateway_config.phase9.example.json"
```

Generate Windows launcher scripts.

```powershell
C:\Python311\python.exe mcpguardianctl.py write-launchers `
  --output-dir "F:\Projects\MCPGuardian\scripts" `
  --root "F:\Projects\MCPGuardian" `
  --python-exe "C:\Python311\python.exe" `
  --gateway-config "F:\Projects\MCPGuardian\config\gateway_config.json" `
  --http-config "F:\Projects\MCPGuardian\config\http_gateway_config.json"
```

## 2. Harden rules before rollout

Start with `config/active_rules.hardened.example.json` if local file mutation risk matters. It blocks native write/edit/move/create-directory, process kill, and dangerous PowerShell patterns. Do not enable broad write access on day one.

Recommended baseline:

```text
read/list/search/screenshot     allow
write/edit/move/delete          block by default
PowerShell admin/destructive    block
rule mutation                   disabled
```

## 3. Claude Desktop migration

Dry run:

```powershell
C:\Python311\python.exe mcpguardianctl.py migrate-claude `
  --config-path "$env:APPDATA\Claude\claude_desktop_config.json" `
  --python-exe "C:\Python311\python.exe" `
  --gateway-script "F:\Projects\MCPGuardian\guardian_gateway.py" `
  --root "F:\Projects\MCPGuardian" `
  --guardian-config "F:\Projects\MCPGuardian\config\gateway_config.json" `
  --dry-run
```

Apply:

```powershell
C:\Python311\python.exe mcpguardianctl.py migrate-claude `
  --config-path "$env:APPDATA\Claude\claude_desktop_config.json" `
  --python-exe "C:\Python311\python.exe" `
  --gateway-script "F:\Projects\MCPGuardian\guardian_gateway.py" `
  --root "F:\Projects\MCPGuardian" `
  --guardian-config "F:\Projects\MCPGuardian\config\gateway_config.json"
```

This removes only these known replaced servers:

```text
desktop-commander
windows-mcp
filesystem
```

Unrelated MCP servers are preserved.

## 4. Rollback

```powershell
C:\Python311\python.exe mcpguardianctl.py rollback-claude `
  --config-path "$env:APPDATA\Claude\claude_desktop_config.json"
```

If needed, pass a specific backup:

```powershell
C:\Python311\python.exe mcpguardianctl.py rollback-claude `
  --config-path "$env:APPDATA\Claude\claude_desktop_config.json" `
  --backup-path "$env:APPDATA\Claude\claude_desktop_config.json.bak_YYYYMMDD_HHMMSS"
```

## 5. ChatGPT HTTP exposure

Generate a token:

```powershell
C:\Python311\python.exe mcpguardianctl.py make-token --bytes 32
```

Start HTTP gateway locally:

```powershell
.\scripts\start_http_gateway.ps1 -BearerToken "<32+ random chars>" -Port 8000
```

Expose only through HTTPS tunnel such as Cloudflare Tunnel. Never expose unauthenticated HTTP.

## 6. Acceptance checklist

Before removing external MCP servers permanently:

- `mcpguardian_gateway_status` returns healthy backend/native status.
- `guardian_read_file` can read a test file under `allowed_roots`.
- `guardian_read_file` rejects paths outside `allowed_roots`.
- `guardian_write_file` is blocked under hardened baseline.
- `guardian_powershell` captures stdout/stderr and blocks dangerous patterns.
- `trace.jsonl` is created for tool calls.
- Claude Desktop config backup exists.
- Rollback command restores the previous config.

## 7. Non-goals

This rollout does not implement OAuth/DCR, automatic Cloudflare Access provisioning, registry mutation tools, or full UI automation. Those remain intentionally outside the production minimum.
