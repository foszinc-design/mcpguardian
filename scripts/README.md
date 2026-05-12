# MCPGuardian Scripts

Launcher scripts are generated per installation because they contain absolute paths.

Generate them with:

```powershell
C:\Python311\python.exe mcpguardianctl.py write-launchers `
  --output-dir "F:\Projects\MCPGuardian\scripts" `
  --root "F:\Projects\MCPGuardian" `
  --python-exe "C:\Python311\python.exe" `
  --gateway-config "F:\Projects\MCPGuardian\config\gateway_config.json" `
  --http-config "F:\Projects\MCPGuardian\config\http_gateway_config.json"
```

Generated scripts:

```text
run_diagnostics.ps1
start_stdio_gateway.ps1
start_http_gateway.ps1
migrate_claude_desktop.ps1
rollback_claude_desktop.ps1
register_http_gateway_task.ps1
unregister_http_gateway_task.ps1
```
