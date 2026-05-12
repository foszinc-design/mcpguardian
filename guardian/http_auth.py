"""ASGI bearer-token authentication for MCPGuardian HTTP gateway.

The middleware is intentionally small and strict:
- empty configured tokens are rejected at construction time
- Authorization must be exactly `Bearer <token>`
- comparison uses hmac.compare_digest
- health paths bypass auth for tunnel/monitor checks
"""
from __future__ import annotations

import hmac
import json
from typing import Any, Iterable


class BearerAuthMiddleware:
    """ASGI middleware enforcing `Authorization: Bearer <token>`.

    `health_paths` are intentionally unauthenticated so Cloudflare Tunnel and
    local monitors can verify process liveness without knowing the MCP token.
    """

    def __init__(self, app: Any, *, token: str, health_paths: Iterable[str] = ("/health",)) -> None:
        expected = (token or "").strip()
        if not expected:
            raise ValueError("MCPGUARDIAN_BEARER_TOKEN is required for HTTP gateway")
        self.app = app
        self.expected = expected
        self.health_paths = set(health_paths)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path in self.health_paths:
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "")
        prefix = "Bearer "
        provided = authorization[len(prefix):] if authorization.startswith(prefix) else ""

        if not provided or not hmac.compare_digest(provided, self.expected):
            await self._reject(send, "invalid or missing bearer token")
            return

        await self.app(scope, receive, send)

    async def _reject(self, send: Any, message: str) -> None:
        body = json.dumps(
            {
                "ok": False,
                "error_code": "UNAUTHORIZED",
                "message": message,
                "run_id": None,
                "artifacts": [],
                "warnings": [],
                "errors": [],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
