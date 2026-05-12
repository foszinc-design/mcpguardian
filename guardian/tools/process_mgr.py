"""Native process management tools."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

from .common import ToolContext, emit, error_code_for_exception, resolve_path, tool_error, tool_ok

_PROCESS_REGISTRY: dict[int, subprocess.Popen] = {}


def _resolve_command(ctx: ToolContext, command: str) -> str:
    if not command:
        raise ValueError("command is required")
    if os.path.isabs(command) or "/" in command or "\\" in command:
        return str(resolve_path(ctx, command, must_exist=True))
    return command


def start_process(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        command = _resolve_command(ctx, str(arguments.get("command") or ""))
        args = arguments.get("args") or []
        if not isinstance(args, list):
            return tool_error(ctx, "INVALID_ARGUMENT", "args must be a list")
        cwd = arguments.get("working_directory") or arguments.get("cwd")
        cwd_s = str(resolve_path(ctx, str(cwd), must_exist=True)) if cwd else None
        detached = bool(arguments.get("detached", True))
        stdout_path = arguments.get("stdout_path")
        stderr_path = arguments.get("stderr_path")
        stdout_handle = None
        stderr_handle = None
        if stdout_path:
            p = resolve_path(ctx, str(stdout_path), must_exist=False)
            p.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = open(p, "ab")
        if stderr_path:
            p = resolve_path(ctx, str(stderr_path), must_exist=False)
            p.parent.mkdir(parents=True, exist_ok=True)
            stderr_handle = open(p, "ab")
        try:
            proc = subprocess.Popen(
                [command] + [str(item) for item in args],
                cwd=cwd_s,
                stdout=stdout_handle or subprocess.PIPE,
                stderr=stderr_handle or subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=False,
                start_new_session=(os.name != "nt" and detached),
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" and detached else 0),
            )
        finally:
            if stdout_handle:
                stdout_handle.close()
            if stderr_handle:
                stderr_handle.close()
        _PROCESS_REGISTRY[proc.pid] = proc
        emit(ctx, "native_process_started", pid=proc.pid, command=command, args=args, cwd=cwd_s)
        return tool_ok(ctx, message="process started", data={"pid": proc.pid, "command": command, "args": args, "detached": detached})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def list_processes(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "").lower()
    max_results = int(arguments.get("max_results") or 100)
    items = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time", "status"]):
        try:
            info = proc.info
            hay = " ".join(str(info.get(key) or "") for key in ["pid", "name", "exe", "cmdline"]).lower()
            if query and query not in hay:
                continue
            items.append(info)
            if len(items) >= max_results:
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return tool_ok(ctx, message="processes listed", data={"processes": items, "truncated": len(items) >= max_results})


def kill_process(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        pid = int(arguments.get("pid"))
        tree = bool(arguments.get("tree", True))
        timeout = float(arguments.get("timeout") or 5)
        proc = psutil.Process(pid)
        targets = proc.children(recursive=True) if tree else []
        targets.append(proc)
        killed = []
        for target in targets:
            try:
                target.terminate()
            except psutil.NoSuchProcess:
                continue
        gone, alive = psutil.wait_procs(targets, timeout=timeout)
        for target in alive:
            try:
                target.kill()
            except psutil.NoSuchProcess:
                pass
        for target in targets:
            killed.append(target.pid)
        popen = _PROCESS_REGISTRY.pop(pid, None)
        if popen is not None:
            with __import__("contextlib").suppress(Exception):
                popen.wait(timeout=timeout)
            for stream in (popen.stdout, popen.stderr, popen.stdin):
                if stream is not None:
                    with __import__("contextlib").suppress(Exception):
                        stream.close()
        emit(ctx, "native_process_killed", pid=pid, tree=tree, killed=killed)
        return tool_ok(ctx, message="process terminated", data={"pid": pid, "tree": tree, "target_pids": killed})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))
