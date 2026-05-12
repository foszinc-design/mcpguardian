"""Shared helpers for native tools."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..mcp_tools import error_envelope, ok_envelope
from ..path_policy import PathPolicy, PathPolicyError
from ..structured_trace import TraceWriter


@dataclass(frozen=True)
class ToolContext:
    policy: PathPolicy
    run_dir: Path
    writer: TraceWriter
    root: Path


def now_ms() -> int:
    return int(time.time() * 1000)


def mcp_text_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            }
        ]
    }
    if is_error:
        out["isError"] = True
    return out


def tool_ok(ctx: ToolContext, *, message: str = "ok", data: Any = None, artifacts: list[str] | None = None, warnings: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload = ok_envelope(run_id=ctx.writer.run_id, message=message, data=data, artifacts=artifacts, warnings=warnings, **extra)
    return mcp_text_result(payload)


def tool_error(ctx: ToolContext, code: str, message: str, *, errors: list[Any] | None = None, warnings: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload = error_envelope(code, message, run_id=ctx.writer.run_id, errors=errors, warnings=warnings, **extra)
    return mcp_text_result(payload, is_error=True)


def resolve_path(ctx: ToolContext, value: str | Path, *, must_exist: bool = False) -> Path:
    return ctx.policy.resolve_allowed(value, must_exist=must_exist)


def read_text_flexible(path: Path, *, encoding: str = "utf-8") -> str:
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        return path.read_text(encoding=encoding, errors="replace")


def emit(ctx: ToolContext, event_type: str, **payload: Any) -> None:
    try:
        ctx.writer.emit(event_type, **payload)
    except Exception:
        pass


def error_code_for_exception(exc: Exception) -> str:
    if isinstance(exc, PathPolicyError):
        return "PATH_POLICY_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "FILE_NOT_FOUND"
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, ValueError):
        return "INVALID_ARGUMENT"
    return "INTERNAL_ERROR"
