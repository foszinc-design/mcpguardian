"""Optional clipboard tools for Phase 7."""
from __future__ import annotations

import subprocess
from typing import Any

from .common import ToolContext, error_code_for_exception, tool_error, tool_ok


def clipboard_write(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    text = str(arguments.get("text") if arguments.get("text") is not None else "")
    try:
        if __import__("os").name == "nt":
            subprocess.run(["clip"], input=text, text=True, encoding="utf-8", check=True)
            return tool_ok(ctx, message="clipboard written", data={"length": len(text)})
        return tool_error(ctx, "UNSUPPORTED_PLATFORM", "guardian_clipboard_write is Windows-only in Phase 7")
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))
