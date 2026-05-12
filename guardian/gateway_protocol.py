"""Minimal JSON-RPC helpers for MCP stdio gateway mode.

Phase 5B deliberately keeps this layer small and testable. It assumes the
common MCP stdio framing used by local servers: one JSON-RPC message per UTF-8
line. It does not implement HTTP/SSE transports.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


JSONRPC_VERSION = "2.0"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


@dataclass(frozen=True)
class JsonRpcRequest:
    id: Any
    method: str
    params: dict[str, Any]


def parse_message(line: bytes | str) -> dict[str, Any]:
    raw = line.decode("utf-8") if isinstance(line, bytes) else line
    raw = raw.strip()
    if not raw:
        raise JsonRpcError(-32700, "Empty JSON-RPC message")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JsonRpcError(-32700, f"Invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise JsonRpcError(-32600, "JSON-RPC message must be an object")
    return obj


def encode_message(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def make_result(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "result": result}


def make_error(message_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "error": error}


def make_request(message_id: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    obj: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": message_id, "method": method}
    if params is not None:
        obj["params"] = params
    return obj


def make_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    obj: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        obj["params"] = params
    return obj
