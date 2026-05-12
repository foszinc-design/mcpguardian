"""HTTP entrypoint for MCPGuardian Gateway Phase 6.5.

This module exposes the same GatewayRouter core used by the stdio gateway over
an authenticated HTTP JSON-RPC endpoint suitable for tunneling through
Cloudflare/ngrok. The stdio entrypoint (`guardian_gateway.py`) is deliberately
untouched.

Security boundary:
- HTTP mode refuses to start without MCPGUARDIAN_BEARER_TOKEN.
- /health is the only unauthenticated endpoint.
- Backend routing, policy gates, resilience, path policy, and trace emission are
  all shared with the stdio gateway through GatewayJsonRpcServer/GatewayRouter.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # Support `python guardian/guardian_http_gateway.py`.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "guardian"

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .atomic_io import load_json
from .gateway_protocol import JsonRpcError, make_error, parse_message
from .gateway_router import GatewayConfig, GatewayRouter
from .gateway_server import GatewayJsonRpcServer
from .http_auth import BearerAuthMiddleware


DEFAULT_VERSION = "1.0"


@dataclass(frozen=True)
class HttpGatewaySettings:
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    require_bearer_token: bool = True
    health_endpoint: bool = True

    @classmethod
    def load(cls, *, root: str | Path | None = None, config_path: str | Path | None = None) -> "HttpGatewaySettings":
        resolved_root = Path(root or os.environ.get("MCPGUARDIAN_ROOT") or Path.cwd()).expanduser().resolve(strict=False)
        raw_config = config_path or os.environ.get("MCPGUARDIAN_CONFIG")
        cfg_path = Path(raw_config).expanduser().resolve(strict=False) if raw_config else resolved_root / "config" / "gateway_config.json"
        cfg = load_json(cfg_path, default={}) if cfg_path.exists() else {}
        http = cfg.get("http", {}) if isinstance(cfg, dict) and isinstance(cfg.get("http", {}), dict) else {}
        env_port = os.environ.get("MCPGUARDIAN_HTTP_PORT")
        path = str(http.get("path", "/mcp"))
        if not path.startswith("/"):
            path = "/" + path
        return cls(
            host=str(http.get("host", "127.0.0.1")),
            port=int(env_port or http.get("port", 8000)),
            path=path,
            require_bearer_token=bool(http.get("require_bearer_token", True)),
            health_endpoint=bool(http.get("health_endpoint", True)),
        )


def require_bearer_token_from_env() -> str:
    token = os.environ.get("MCPGUARDIAN_BEARER_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MCPGUARDIAN_BEARER_TOKEN is required; refusing unauthenticated HTTP gateway startup")
    return token


def create_http_app(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    bearer_token: str | None = None,
    version: str = DEFAULT_VERSION,
) -> Starlette:
    """Create an authenticated ASGI app for the HTTP Gateway.

    Tests call this directly; production `main()` serves the app on localhost
    behind Cloudflare Tunnel or another HTTPS reverse proxy.
    """
    settings = HttpGatewaySettings.load(root=root, config_path=config_path)
    if settings.require_bearer_token:
        token = bearer_token if bearer_token is not None else require_bearer_token_from_env()
    else:
        token = bearer_token or "dev-token-for-tests-only"

    cfg = GatewayConfig.load(root=root, config_path=config_path)
    router = GatewayRouter(cfg)
    server = GatewayJsonRpcServer(router)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "version": version, "backends": len(router.clients)})

    async def mcp_endpoint(request: Request) -> Response:
        try:
            body = await request.body()
            message = parse_message(body)
            response = await server.handle(message)
        except JsonRpcError as exc:
            return JSONResponse(make_error(None, exc.code, exc.message, exc.data), status_code=400)
        except Exception as exc:
            return JSONResponse(make_error(None, -32603, str(exc)), status_code=500)

        if response is None:
            # JSON-RPC notifications intentionally have no response payload.
            return Response(status_code=202)
        return JSONResponse(response)

    routes: list[Route] = []
    if settings.health_endpoint:
        routes.append(Route("/health", health, methods=["GET"]))
    routes.append(Route(settings.path, mcp_endpoint, methods=["POST"]))

    @asynccontextmanager
    async def lifespan(_: Starlette):
        try:
            yield
        finally:
            await router.shutdown()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(BearerAuthMiddleware, token=token, health_paths=("/health",))
    app.state.gateway_router = router
    app.state.gateway_jsonrpc_server = server
    app.state.http_settings = settings
    return app


def main() -> int:
    settings = HttpGatewaySettings.load()
    token = require_bearer_token_from_env() if settings.require_bearer_token else os.environ.get("MCPGUARDIAN_BEARER_TOKEN", "dev")
    app = create_http_app(bearer_token=token)
    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is required for guardian_http_gateway.py") from exc
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
