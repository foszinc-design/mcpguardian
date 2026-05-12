# MCPGuardian Phase 6.5: HTTP Remote Adapter + Cloudflare Tunnel

Phase 6.5 exposes the existing MCPGuardian Gateway over authenticated HTTP so a remote MCP client can reach it through a HTTPS tunnel.

The stdio entrypoint is unchanged:

```text
guardian_gateway.py
```

The HTTP entrypoint is added:

```text
guardian_http_gateway.py
guardian/guardian_http_gateway.py
```

## Security boundary

HTTP mode refuses to start without `MCPGUARDIAN_BEARER_TOKEN`.

Do not expose `127.0.0.1:8000` directly to the public internet. The intended topology is:

```text
ChatGPT Desktop / Custom MCP App
  -> HTTPS public hostname
  -> Cloudflare Tunnel
  -> http://127.0.0.1:8000/mcp on System1
  -> MCPGuardian Gateway
  -> backend MCP servers over stdio
```

`/health` is intentionally unauthenticated for tunnel and monitoring checks. `/mcp` requires `Authorization: Bearer <token>`.

## Files added

```text
guardian/guardian_http_gateway.py
guardian/http_auth.py
guardian_http_gateway.py
config/http_gateway_config.example.json
config/cloudflared_config.example.yml
tests/test_http_gateway.py
tests/test_http_auth.py
```

## Environment variables

```text
MCPGUARDIAN_ROOT=F:\Projects\MCPGuardian
MCPGUARDIAN_CONFIG=F:\Projects\MCPGuardian\config\gateway_config.json
MCPGUARDIAN_HTTP_PORT=8000
MCPGUARDIAN_BEARER_TOKEN=<32+ random chars>
MCPGUARDIAN_ENABLE_RULE_MUTATION=0
```

`MCPGUARDIAN_BEARER_TOKEN` is mandatory when `http.require_bearer_token` is true.

## Start local HTTP gateway

```powershell
$env:MCPGUARDIAN_ROOT = "F:\Projects\MCPGuardian"
$env:MCPGUARDIAN_CONFIG = "F:\Projects\MCPGuardian\config\gateway_config.json"
$env:MCPGUARDIAN_HTTP_PORT = "8000"
$env:MCPGUARDIAN_BEARER_TOKEN = "replace-with-32-plus-random-characters"
$env:MCPGUARDIAN_ENABLE_RULE_MUTATION = "0"

C:\Python311\python.exe F:\Projects\MCPGuardian\guardian_http_gateway.py
```

The service listens only on localhost by default:

```text
http://127.0.0.1:8000/mcp
```

Health check:

```powershell
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"ok":true,"version":"1.0","backends":1}
```

## Cloudflare Tunnel setup on Windows

Install `cloudflared`:

```powershell
winget install --id Cloudflare.cloudflared
```

Authenticate:

```powershell
cloudflared tunnel login
```

Create tunnel:

```powershell
cloudflared tunnel create mcpguardian
```

Create config:

```yaml
tunnel: mcpguardian
credentials-file: C:\Users\<USER>\.cloudflared\<TUNNEL_ID>.json

ingress:
  - hostname: mcpguardian.yourdomain.com
    service: http://127.0.0.1:8000
    originRequest:
      noTLSVerify: false
  - service: http_status:404
```

Route DNS:

```powershell
cloudflared tunnel route dns mcpguardian mcpguardian.yourdomain.com
```

Run tunnel:

```powershell
cloudflared tunnel run mcpguardian
```

Optional service install:

```powershell
cloudflared service install
```

## ChatGPT custom MCP app registration

1. Open ChatGPT settings.
2. Go to **Apps & Connectors**.
3. Enable **Advanced / Developer Mode**.
4. Add a custom connector.
5. URL:

```text
https://mcpguardian.yourdomain.com/mcp
```

6. Auth:

```text
Bearer token
```

7. Use the exact value from `MCPGUARDIAN_BEARER_TOKEN`.

## Security checklist

- Use a 32+ character random bearer token.
- Keep `MCPGUARDIAN_ENABLE_RULE_MUTATION=0` for ChatGPT access.
- Keep `http.host` as `127.0.0.1`.
- Use Cloudflare Tunnel or an equivalent HTTPS reverse proxy.
- Add Cloudflare Access where possible.
- Keep `allowed_roots` minimal.
- Keep write/delete/move tools blocked by active gateway rules until explicit approval workflow exists.
- Do not allow full-drive filesystem access.
- Review `runs/*/trace.jsonl` because remote access to local files is a high-trust operation.

## Protocol note

The HTTP adapter reuses `GatewayJsonRpcServer` and `GatewayRouter`, the same core used by stdio mode. This preserves tool discovery, prefix namespacing, preflight policy, path policy, resilience, circuit breaker state, and trace emission.

The adapter is intentionally implemented as an ASGI HTTP endpoint so dynamic backend tool discovery is returned exactly from `tools/list`. This avoids duplicating backend tools as static decorators and keeps stdio and HTTP behavior aligned.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Current result:

```text
Ran 57 tests in 20.982s

OK
```
