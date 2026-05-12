"""Native screenshot tool."""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from .common import ToolContext, emit, error_code_for_exception, resolve_path, tool_error, tool_ok


def screenshot(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    output = arguments.get("output_path") or arguments.get("path") or str(ctx.run_dir / "screenshot.png")
    try:
        output_path = resolve_path(ctx, str(output), must_exist=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        bbox = None
        if all(key in arguments for key in ["left", "top", "width", "height"]):
            left = int(arguments["left"])
            top = int(arguments["top"])
            width = int(arguments["width"])
            height = int(arguments["height"])
            bbox = (left, top, left + width, top + height)
        try:
            from PIL import ImageGrab  # type: ignore
        except Exception as exc:
            return tool_error(ctx, "DEPENDENCY_MISSING", "Pillow/ImageGrab is required for guardian_screenshot", detail=str(exc), platform=platform.system())
        try:
            image = ImageGrab.grab(bbox=bbox)
            image.save(output_path, format="PNG")
        except Exception as exc:
            return tool_error(ctx, "SCREENSHOT_FAILED", str(exc), platform=platform.system())
        emit(ctx, "native_screenshot_saved", path=str(output_path), bbox=bbox)
        return tool_ok(ctx, message="screenshot saved", artifacts=[str(output_path)], data={"path": str(output_path), "bbox": bbox, "platform": platform.system()})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))
