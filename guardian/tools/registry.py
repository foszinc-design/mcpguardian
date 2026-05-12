"""Native tool registry for Phase 7.

This registry lets GatewayRouter expose Python-native tools alongside proxied
backend MCP tools. It deliberately returns standard MCP tools/list entries and
MCP tools/call result objects so stdio and HTTP transports share the same core.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .common import ToolContext
from .file_ops import (
    create_directory,
    directory_tree,
    edit_file,
    get_file_info,
    list_directory,
    move_file,
    read_file,
    read_multiple_files,
    search_files,
    write_file,
)
from .powershell import run_powershell
from .process_mgr import kill_process, list_processes, start_process
from .screenshot import screenshot
from .document_ops import inspect_xlsx, read_docx, inspect_pdf

ToolCallable = Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class NativeTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolCallable
    destructive: bool = False

    def to_mcp_tool(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


class NativeToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, NativeTool] = {}
        for tool in _build_tools():
            self._tools[tool.name] = tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.to_mcp_tool() for tool in sorted(self._tools.values(), key=lambda item: item.name)]

    def call(self, name: str, ctx: ToolContext, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name].handler(ctx, arguments or {})

    def tool(self, name: str) -> NativeTool:
        return self._tools[name]


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _build_tools() -> list[NativeTool]:
    path_prop = {"type": "string", "description": "Path under MCPGuardian allowed_roots."}
    return [
        NativeTool(
            "guardian_powershell",
            "Run a PowerShell command with timeout, UTF-8 capture, dangerous-command checks, path policy, preflight, and trace.",
            _schema(
                {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 60},
                    "working_directory": path_prop,
                },
                ["command"],
            ),
            run_powershell,
            destructive=True,
        ),
        NativeTool(
            "guardian_read_file",
            "Read a text file with optional offset and length. For binary Office/PDF parsing use a later dedicated validator/tool.",
            _schema(
                {
                    "path": path_prop,
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "length": {"type": "integer", "minimum": 0},
                    "encoding": {"type": "string", "default": "utf-8"},
                },
                ["path"],
            ),
            read_file,
        ),
        NativeTool(
            "guardian_read_multiple_files",
            "Read multiple text files under allowed_roots.",
            _schema({"paths": {"type": "array", "items": path_prop}, "length": {"type": "integer"}, "encoding": {"type": "string", "default": "utf-8"}}, ["paths"]),
            read_multiple_files,
        ),
        NativeTool(
            "guardian_write_file",
            "Write or append a text file using atomic rewrite semantics and path policy.",
            _schema(
                {
                    "path": path_prop,
                    "content": {"type": "string"},
                    "mode": {"type": "string", "enum": ["rewrite", "append"], "default": "rewrite"},
                    "encoding": {"type": "string", "default": "utf-8"},
                },
                ["path", "content"],
            ),
            write_file,
            destructive=True,
        ),
        NativeTool(
            "guardian_edit_file",
            "Edit a text file by exact string replacement, equivalent to Desktop Commander edit_block semantics.",
            _schema(
                {
                    "path": path_prop,
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                    "expected_replacements": {"type": "integer", "minimum": 0},
                    "encoding": {"type": "string", "default": "utf-8"},
                },
                ["path", "old_text", "new_text"],
            ),
            edit_file,
            destructive=True,
        ),
        NativeTool("guardian_list_directory", "List one directory under allowed_roots.", _schema({"path": path_prop}, ["path"]), list_directory),
        NativeTool(
            "guardian_directory_tree",
            "Return a recursive directory tree under allowed_roots with max depth and result caps.",
            _schema({"path": path_prop, "max_depth": {"type": "integer", "default": 3}, "max_entries": {"type": "integer", "default": 500}}, ["path"]),
            directory_tree,
        ),
        NativeTool(
            "guardian_move_file",
            "Move or rename a file/directory under allowed_roots.",
            _schema({"source": path_prop, "destination": path_prop, "overwrite": {"type": "boolean", "default": False}}, ["source", "destination"]),
            move_file,
            destructive=True,
        ),
        NativeTool("guardian_create_directory", "Create a directory under allowed_roots.", _schema({"path": path_prop, "parents": {"type": "boolean", "default": True}, "exist_ok": {"type": "boolean", "default": True}}, ["path"]), create_directory, destructive=True),
        NativeTool("guardian_get_file_info", "Return file metadata and SHA-256 for files.", _schema({"path": path_prop}, ["path"]), get_file_info),
        NativeTool(
            "guardian_search_files",
            "Search files by filename pattern and optional literal content query under allowed_roots.",
            _schema({"path": path_prop, "pattern": {"type": "string", "default": "*"}, "content_query": {"type": "string"}, "max_results": {"type": "integer", "default": 100}}, ["path"]),
            search_files,
        ),
        NativeTool(
            "guardian_start_process",
            "Start a process. Absolute executable paths must be under allowed_roots; plain executable names are allowed for PATH lookup.",
            _schema(
                {
                    "command": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}, "default": []},
                    "working_directory": path_prop,
                    "detached": {"type": "boolean", "default": True},
                    "stdout_path": path_prop,
                    "stderr_path": path_prop,
                },
                ["command"],
            ),
            start_process,
            destructive=True,
        ),
        NativeTool("guardian_list_processes", "List running processes via psutil.", _schema({"query": {"type": "string"}, "max_results": {"type": "integer", "default": 100}}), list_processes),
        NativeTool("guardian_kill_process", "Terminate a process by PID; use active_rules to gate this in production.", _schema({"pid": {"type": "integer"}, "tree": {"type": "boolean", "default": True}, "timeout": {"type": "number", "default": 5}}, ["pid"]), kill_process, destructive=True),
        NativeTool(
            "guardian_inspect_xlsx",
            "Inspect XLSX/XLSM workbook metadata and sample rows without claiming analytical coverage.",
            _schema({"path": path_prop, "sample_rows": {"type": "integer", "default": 5}, "scan_all": {"type": "boolean", "default": False}}, ["path"]),
            inspect_xlsx,
        ),
        NativeTool(
            "guardian_read_docx",
            "Extract plain text from DOCX document.xml using stdlib zip/xml parsing.",
            _schema({"path": path_prop, "max_chars": {"type": "integer", "default": 20000}}, ["path"]),
            read_docx,
        ),
        NativeTool(
            "guardian_inspect_pdf",
            "Inspect PDF metadata, estimated page count, and conservative text hints. Full PDF text extraction is not claimed.",
            _schema({"path": path_prop}, ["path"]),
            inspect_pdf,
        ),
        NativeTool(
            "guardian_screenshot",
            "Capture a PNG screenshot to an allowed output path. Requires Pillow/ImageGrab and desktop session access.",
            _schema(
                {
                    "output_path": path_prop,
                    "left": {"type": "integer"},
                    "top": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                }
            ),
            screenshot,
        ),
        NativeTool("guardian_wait", "Sleep for a bounded number of milliseconds.", _schema({"milliseconds": {"type": "integer", "minimum": 0, "maximum": 300000}}, ["milliseconds"]), _wait_tool),
    ]


def _wait_tool(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    import time
    from .common import tool_error, tool_ok

    ms = int(arguments.get("milliseconds") or 0)
    if ms < 0 or ms > 300000:
        return tool_error(ctx, "INVALID_ARGUMENT", "milliseconds must be between 0 and 300000")
    time.sleep(ms / 1000.0)
    return tool_ok(ctx, message="wait completed", data={"milliseconds": ms})
