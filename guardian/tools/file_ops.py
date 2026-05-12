"""Native filesystem tools for MCPGuardian."""
from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_text, sha256_file
from .common import ToolContext, emit, error_code_for_exception, read_text_flexible, resolve_path, tool_error, tool_ok


def read_file(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_path(ctx, str(arguments.get("path") or ""), must_exist=True)
        if not path.is_file():
            return tool_error(ctx, "NOT_A_FILE", f"Not a file: {path}")
        offset = int(arguments.get("offset") or 0)
        length_raw = arguments.get("length")
        length = int(length_raw) if length_raw is not None else None
        encoding = str(arguments.get("encoding") or "utf-8")
        text = read_text_flexible(path, encoding=encoding)
        sliced = text[offset : offset + length if length is not None else None]
        data = {
            "path": str(path),
            "text": sliced,
            "offset": offset,
            "length": len(sliced),
            "total_length": len(text),
            "truncated": len(sliced) != len(text),
        }
        emit(ctx, "native_file_read", path=str(path), bytes=path.stat().st_size)
        return tool_ok(ctx, message="file read", data=data)
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def read_multiple_files(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    paths = arguments.get("paths") or []
    if not isinstance(paths, list) or not paths:
        return tool_error(ctx, "INVALID_ARGUMENT", "guardian_read_multiple_files requires non-empty paths list")
    results = []
    errors = []
    for raw in paths:
        res = read_file(ctx, {"path": raw, "length": arguments.get("length"), "encoding": arguments.get("encoding", "utf-8")})
        payload = _payload_from_result(res)
        if payload.get("ok"):
            results.append(payload.get("data"))
        else:
            errors.append({"path": raw, "error": payload})
    if errors:
        return tool_error(ctx, "PARTIAL_READ_FAILURE", "One or more files could not be read", data={"files": results}, errors=errors)
    return tool_ok(ctx, message="files read", data={"files": results})


def write_file(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_path(ctx, str(arguments.get("path") or ""), must_exist=False)
        mode = str(arguments.get("mode") or "rewrite")
        content = str(arguments.get("content") if arguments.get("content") is not None else "")
        encoding = str(arguments.get("encoding") or "utf-8")
        if mode not in {"rewrite", "append"}:
            return tool_error(ctx, "INVALID_ARGUMENT", "mode must be rewrite or append")
        if mode == "append" and path.exists():
            existing = read_text_flexible(path, encoding=encoding)
            content_to_write = existing + content
        else:
            content_to_write = content
        atomic_write_text(path, content_to_write, encoding=encoding)
        emit(ctx, "native_file_written", path=str(path), mode=mode, bytes=len(content.encode(encoding, errors="replace")))
        return tool_ok(ctx, message="file written", data={"path": str(path), "mode": mode, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def edit_file(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_path(ctx, str(arguments.get("path") or ""), must_exist=True)
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if old_text is None or new_text is None:
            return tool_error(ctx, "INVALID_ARGUMENT", "guardian_edit_file requires old_text and new_text")
        old_s = str(old_text)
        new_s = str(new_text)
        replace_all = bool(arguments.get("replace_all", False))
        expected = arguments.get("expected_replacements")
        expected_count = int(expected) if expected is not None else None
        encoding = str(arguments.get("encoding") or "utf-8")
        text = read_text_flexible(path, encoding=encoding)
        count = text.count(old_s)
        if count == 0:
            return tool_error(ctx, "TEXT_NOT_FOUND", "old_text was not found")
        if expected_count is not None and count != expected_count:
            return tool_error(ctx, "REPLACEMENT_COUNT_MISMATCH", f"expected {expected_count} replacements, found {count}")
        if count > 1 and not replace_all and expected_count != 1:
            return tool_error(ctx, "AMBIGUOUS_REPLACEMENT", f"old_text occurs {count} times; set replace_all=true or expected_replacements=1")
        if replace_all:
            updated = text.replace(old_s, new_s)
            replacements = count
        else:
            updated = text.replace(old_s, new_s, 1)
            replacements = 1
        atomic_write_text(path, updated, encoding=encoding)
        emit(ctx, "native_file_edited", path=str(path), replacements=replacements)
        return tool_ok(ctx, message="file edited", data={"path": str(path), "replacements": replacements, "sha256": sha256_file(path)})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def list_directory(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_path(ctx, str(arguments.get("path") or "."), must_exist=True)
        if not path.is_dir():
            return tool_error(ctx, "NOT_A_DIRECTORY", f"Not a directory: {path}")
        entries = [_entry_info(child) for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))]
        return tool_ok(ctx, message="directory listed", data={"path": str(path), "entries": entries})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def directory_tree(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_path(ctx, str(arguments.get("path") or "."), must_exist=True)
        max_depth = int(arguments.get("max_depth") or arguments.get("depth") or 3)
        max_entries = int(arguments.get("max_entries") or 500)
        entries: list[dict[str, Any]] = []
        for child in _walk_limited(path, max_depth=max_depth, max_entries=max_entries):
            entries.append(_entry_info(child, root=path))
        return tool_ok(ctx, message="directory tree built", data={"path": str(path), "max_depth": max_depth, "entries": entries, "truncated": len(entries) >= max_entries})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def move_file(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        source = resolve_path(ctx, str(arguments.get("source") or arguments.get("src") or ""), must_exist=True)
        destination = resolve_path(ctx, str(arguments.get("destination") or arguments.get("dst") or ""), must_exist=False)
        if destination.exists() and not bool(arguments.get("overwrite", False)):
            return tool_error(ctx, "DESTINATION_EXISTS", f"Destination exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        emit(ctx, "native_file_moved", source=str(source), destination=str(destination))
        return tool_ok(ctx, message="file moved", data={"source": str(source), "destination": str(destination)})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def create_directory(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_path(ctx, str(arguments.get("path") or ""), must_exist=False)
        path.mkdir(parents=bool(arguments.get("parents", True)), exist_ok=bool(arguments.get("exist_ok", True)))
        emit(ctx, "native_directory_created", path=str(path))
        return tool_ok(ctx, message="directory created", data={"path": str(path)})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def get_file_info(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_path(ctx, str(arguments.get("path") or ""), must_exist=True)
        info = _entry_info(path)
        if path.is_file():
            info["sha256"] = sha256_file(path)
        return tool_ok(ctx, message="file info", data=info)
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def search_files(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        root = resolve_path(ctx, str(arguments.get("path") or arguments.get("root") or "."), must_exist=True)
        if not root.is_dir():
            return tool_error(ctx, "NOT_A_DIRECTORY", f"Not a directory: {root}")
        pattern = str(arguments.get("pattern") or arguments.get("name_pattern") or "*")
        content_query = arguments.get("content_query")
        max_results = int(arguments.get("max_results") or 100)
        results = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".git")]
            for filename in filenames:
                if not fnmatch.fnmatch(filename, pattern):
                    continue
                candidate = Path(dirpath) / filename
                if content_query:
                    try:
                        text = read_text_flexible(candidate)
                    except Exception:
                        continue
                    if str(content_query) not in text:
                        continue
                results.append(_entry_info(candidate, root=root))
                if len(results) >= max_results:
                    return tool_ok(ctx, message="files searched", data={"root": str(root), "results": results, "truncated": True})
        return tool_ok(ctx, message="files searched", data={"root": str(root), "results": results, "truncated": False})
    except Exception as exc:
        return tool_error(ctx, error_code_for_exception(exc), str(exc))


def _entry_info(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    stat = path.stat()
    data = {
        "name": path.name,
        "path": str(path),
        "type": "directory" if path.is_dir() else "file",
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
    }
    if root is not None:
        try:
            data["relative_path"] = str(path.relative_to(root))
        except ValueError:
            pass
    return data


def _walk_limited(root: Path, *, max_depth: int, max_entries: int):
    count = 0
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        depth = len(current.parts) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        for name in sorted(dirnames + filenames):
            yield current / name
            count += 1
            if count >= max_entries:
                return


def _payload_from_result(result: dict[str, Any]) -> dict[str, Any]:
    try:
        return __import__("json").loads(result["content"][0]["text"])
    except Exception:
        return {"ok": False, "error_code": "BAD_TOOL_RESULT", "message": str(result)}
