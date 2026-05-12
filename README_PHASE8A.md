# MCPGuardian Phase 8A — Packaging, Migration, Diagnostics

## Goal

Phase 8A turns the Phase 7 development build into an operable local product. It does not add new policy semantics. It reduces rollout risk.

## Added modules

```text
guardian/packaging/config_migration.py
guardian/packaging/diagnostics.py
```

## Claude Desktop migration

Dry run:

```powershell
C:\Python311\python.exe -m guardian.packaging.config_migration `
  --config-path "$env:APPDATA\Claude\claude_desktop_config.json" `
  --python-exe "C:\Python311\python.exe" `
  --gateway-script "F:\Projects\MCPGuardian\guardian_gateway.py" `
  --root "F:\Projects\MCPGuardian" `
  --guardian-config "F:\Projects\MCPGuardian\config\gateway_config.json" `
  --dry-run
```

Apply:

```powershell
C:\Python311\python.exe -m guardian.packaging.config_migration `
  --config-path "$env:APPDATA\Claude\claude_desktop_config.json" `
  --python-exe "C:\Python311\python.exe" `
  --gateway-script "F:\Projects\MCPGuardian\guardian_gateway.py" `
  --root "F:\Projects\MCPGuardian" `
  --guardian-config "F:\Projects\MCPGuardian\config\gateway_config.json"
```

The migration preserves unrelated MCP servers, removes only known replaced backends, and creates a timestamped backup before write.

Removed backend names:

```text
desktop-commander
windows-mcp
filesystem
```

## Diagnostics

```powershell
C:\Python311\python.exe -m guardian.packaging.diagnostics `
  --root "F:\Projects\MCPGuardian" `
  --config-path "F:\Projects\MCPGuardian\config\gateway_config.json"
```

HTTP exposure check:

```powershell
C:\Python311\python.exe -m guardian.packaging.diagnostics `
  --root "F:\Projects\MCPGuardian" `
  --config-path "F:\Projects\MCPGuardian\config\http_gateway_config.json" `
  --http
```

`--http` requires `MCPGUARDIAN_BEARER_TOKEN` to be 32+ characters.

## Rollback

Restore the `.bak_YYYYMMDD_HHMMSS` copy produced next to `claude_desktop_config.json`, then restart Claude Desktop.
