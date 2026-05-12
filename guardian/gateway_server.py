"""STDIO MCP Gateway entrypoint for Phase 5B.

This server exposes aggregated backend MCP tools and routes every tools/call
through MCPGuardian policy evaluation before forwarding it to the backend.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "guardian"

from .gateway_protocol import JsonRpcError, encode_message, make_error, make_result, parse_message
from .gateway_router import GatewayConfig, GatewayRouter


class GatewayJsonRpcServer:
    def __init__(self, router: GatewayRouter) -> None:
        self.router = router
        self._shutdown = False

    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if method is None:
            return None
        try:
            if method == "initialize":
                return make_result(
                    message_id,
                    {
                        "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "mcpguardian-gateway", "version": "5b"},
                    },
                )
            if method == "notifications/initialized":
                await self.router.initialize()
                return None
            if method == "ping":
                return make_result(message_id, {})
            if method == "tools/list":
                tools = await self.router.list_tools()
                return make_result(message_id, {"tools": tools})
            if method == "tools/call":
                name = params.get("name")
                if not isinstance(name, str):
                    raise JsonRpcError(-32602, "tools/call requires params.name")
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    raise JsonRpcError(-32602, "tools/call params.arguments must be an object")
                result = await self.router.call_tool(name, args)
                return make_result(message_id, result)
            if method == "shutdown":
                self._shutdown = True
                return make_result(message_id, {})
            if method == "exit":
                self._shutdown = True
                return None
            raise JsonRpcError(-32601, f"Unsupported method: {method}")
        except JsonRpcError as exc:
            return make_error(message_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            return make_error(message_id, -32603, str(exc))


async def serve_stdio(*, root: str | None = None, config_path: str | None = None) -> int:
    cfg = GatewayConfig.load(root=root, config_path=config_path)
    router = GatewayRouter(cfg)
    server = GatewayJsonRpcServer(router)
    try:
        while not server._shutdown:
            line = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not line:
                break
            try:
                msg = parse_message(line)
            except JsonRpcError as exc:
                sys.stdout.buffer.write(encode_message(make_error(None, exc.code, exc.message, exc.data)))
                sys.stdout.buffer.flush()
                continue
            response = await server.handle(msg)
            if response is not None:
                sys.stdout.buffer.write(encode_message(response))
                sys.stdout.buffer.flush()
    finally:
        await router.shutdown()
    return 0


def main() -> int:
    root = os.environ.get("MCPGUARDIAN_ROOT")
    config_path = os.environ.get("MCPGUARDIAN_CONFIG")
    return asyncio.run(serve_stdio(root=root, config_path=config_path))


if __name__ == "__main__":
    raise SystemExit(main())
