"""Native PowerShell command execution tool."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .common import ToolContext, emit, error_code_for_exception, resolve_path, tool_error, tool_ok

DANGEROUS_PATTERNS = [
    re.compile(r"\bFormat-Disk\b", re.I),
    re.compile(r"\bClear-Disk\b", re.I),
    re.compile(r"\bRemove-Partition\b", re.I),
    re.compile(r"\bRemove-Item\b[^\n]*(?:-Recurse|/s)", re.I),
    re.compile(r"\bdel\b[^\n]*(?:/s|/q)", re.I),
    re.compile(r"\brm\s+-rf\s+(?:/|[A-Za-z]:\\)", re.I),
    re.compile(r"\bSet-ExecutionPolicy\b", re.I),
    re.compile(r"\breg\s+(?:delete|add)\b", re.I),
    re.compile(r"\bbcdedit\b", re.I),
]


def detect_dangerous_command(command: str) -> str | None:
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return pattern.pattern
    return None


def _build_command(command: str) -> list[str]:
    if os.name == "nt":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    # Test/development fallback. Production Windows uses PowerShell above.
    pwsh = shutil.which("pwsh")
    if pwsh:
        return [pwsh, "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-lc", command]


def run_powershell(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    command = str(arguments.get("command") or "").strip()
    if not command:
        return tool_error(ctx, "INVALID_ARGUMENT", "guardian_powershell requires command")
    timeout = int(arguments.get("timeout") or 60)
    if timeout < 1 or timeout > 3600:
        return tool_error(ctx, "INVALID_ARGUMENT", "timeout must be between 1 and 3600 seconds")
    working_directory = arguments.get("working_directory")
    cwd: str | None = None
    if working_directory:
        cwd = str(resolve_path(ctx, str(working_directory), must_exist=True))
    hit = detect_dangerous_command(command)
    if hit:
        emit(ctx, "native_tool_blocked", tool="guardian_powershell", reason="dangerous_command", pattern=hit)
        return tool_error(ctx, "DANGEROUS_COMMAND_BLOCKED", "PowerShell command matched a dangerous command pattern", pattern=hit)
    start = time.monotonic()
    cmd = _build_command(command)
    emit(ctx, "native_tool_command_started", tool="guardian_powershell", command=command, cwd=cwd, platform=platform.system())
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        data = {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "return_code": completed.returncode,
            "duration_ms": duration_ms,
            "platform": platform.system(),
        }
        emit(ctx, "native_tool_command_finished", tool="guardian_powershell", return_code=completed.returncode, duration_ms=duration_ms)
        if completed.returncode != 0:
            return tool_error(ctx, "COMMAND_FAILED", "PowerShell command returned a non-zero exit code", data=data)
        return tool_ok(ctx, message="PowerShell command completed", data=data)
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        emit(ctx, "native_tool_command_timeout", tool="guardian_powershell", timeout=timeout, duration_ms=duration_ms)
        return tool_error(
            ctx,
            "COMMAND_TIMEOUT",
            f"PowerShell command timed out after {timeout} seconds",
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration_ms=duration_ms,
        )
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))
