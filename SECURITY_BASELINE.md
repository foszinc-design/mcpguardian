# MCPGuardian Security Baseline

## Hard rule

MCPGuardian gives an AI client local capability. Treat it as privileged remote execution over your workstation.

## Required controls

1. Keep `allowed_roots` minimal.
2. Disable rule mutation by default.
3. Block write/edit/move/delete until explicitly needed.
4. Block destructive PowerShell patterns.
5. Require bearer token for HTTP mode.
6. Put HTTP mode behind HTTPS and an external access layer such as Cloudflare Access.
7. Keep trace logging enabled.
8. Review `pending_rules.json` manually before approval.

## Recommended first-day rule posture

Use `config/active_rules.hardened.example.json` as a template.

## Things not to do

- Do not expose `http://127.0.0.1:8000` through a tunnel without auth.
- Do not allow full-drive `C:\` or `F:\` unless you are intentionally granting that scope.
- Do not enable `MCPGUARDIAN_ENABLE_RULE_MUTATION=1` for normal ChatGPT/Claude sessions.
- Do not allow `guardian_powershell` to run arbitrary admin commands without active rules.
- Do not delete Claude Desktop config backups until the new topology has been stable for several sessions.
