"""Windows event guard for MCPGuardian Phase 9.

This module is intentionally platform-safe: on non-Windows hosts it still
supports deterministic tests and manual event injection, but native WMI/device
watchers remain disabled. The guard's job is to debounce device-change storms
and temporarily pause WMI/UI-heavy tools while the OS settles.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic_io import load_json, locked_atomic_write_json

DEFAULT_PAUSE_TOOL_PATTERNS = [r"windows.*", r".*screenshot.*", r".*powershell.*", r".*app.*", r".*clipboard.*"]


@dataclass
class WindowsEventGuardConfig:
    enabled: bool = False
    debounce_seconds: float = 8.0
    pause_backends: list[str] = field(default_factory=lambda: ["windows-mcp", "windows_mcp", "native"])
    pause_tool_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_PAUSE_TOOL_PATTERNS))
    state_file: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "WindowsEventGuardConfig":
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            debounce_seconds=float(raw.get("debounce_seconds", 8.0)),
            pause_backends=[str(x) for x in raw.get("pause_backends", ["windows-mcp", "windows_mcp", "native"])],
            pause_tool_patterns=[str(x) for x in raw.get("pause_tool_patterns", DEFAULT_PAUSE_TOOL_PATTERNS)],
            state_file=str(raw.get("state_file")) if raw.get("state_file") else None,
        )


class WindowsEventGuard:
    def __init__(self, config: WindowsEventGuardConfig, *, root: str | Path | None = None) -> None:
        self.config = config
        self.root = Path(root or Path.cwd()).resolve(strict=False)
        self._last_event: dict[str, Any] | None = None
        self._paused_until = 0.0
        self._drive_snapshot: dict[str, str] = {}
        if config.state_file:
            self._load_state()

    @property
    def is_windows(self) -> bool:
        return platform.system().lower() == "windows"

    def record_device_change(self, *, event_type: str, device_id: str = "", timestamp: float | None = None) -> dict[str, Any]:
        now = float(timestamp if timestamp is not None else time.time())
        is_relevant = self._is_relevant_device_event(event_type, device_id)
        if self.config.enabled and is_relevant:
            self._paused_until = max(self._paused_until, now + self.config.debounce_seconds)
        self._last_event = {
            "event_type": event_type,
            "device_id": device_id,
            "timestamp": now,
            "relevant": is_relevant,
            "paused_until": self._paused_until,
        }
        self._save_state()
        return self.status(now=now)

    def should_pause(self, *, backend: str, tool_name: str, now: float | None = None) -> bool:
        if not self.config.enabled:
            return False
        current = float(now if now is not None else time.time())
        if current >= self._paused_until:
            return False
        backend_hit = backend in set(self.config.pause_backends)
        tool_hit = any(re.search(pattern, tool_name, flags=re.I) for pattern in self.config.pause_tool_patterns)
        return backend_hit or tool_hit

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now if now is not None else time.time())
        remaining = max(0.0, self._paused_until - current)
        return {
            "ok": True,
            "enabled": self.config.enabled,
            "platform": platform.system(),
            "native_watch_supported": self.is_windows,
            "paused": remaining > 0,
            "pause_remaining_seconds": round(remaining, 3),
            "paused_until": self._paused_until,
            "last_event": self._last_event,
            "pause_backends": self.config.pause_backends,
            "pause_tool_patterns": self.config.pause_tool_patterns,
            "drive_snapshot": self._drive_snapshot,
        }

    def capture_drive_snapshot(self, drives: dict[str, str] | None = None) -> dict[str, Any]:
        if drives is None:
            drives = _current_drive_snapshot()
        self._drive_snapshot = dict(drives)
        self._save_state()
        return {"ok": True, "drive_snapshot": self._drive_snapshot}

    def compare_drive_snapshot(self, drives: dict[str, str] | None = None) -> dict[str, Any]:
        current = dict(drives if drives is not None else _current_drive_snapshot())
        before = self._drive_snapshot
        added = {k: v for k, v in current.items() if k not in before}
        removed = {k: v for k, v in before.items() if k not in current}
        changed = {k: {"before": before[k], "after": current[k]} for k in before.keys() & current.keys() if before[k] != current[k]}
        return {"ok": True, "added": added, "removed": removed, "changed": changed, "current": current, "previous": before}

    def _is_relevant_device_event(self, event_type: str, device_id: str) -> bool:
        text = f"{event_type} {device_id}".lower()
        return any(token in text for token in ["pnp", "usb", "device", "volume", "drive", "disk"])

    def _state_path(self) -> Path | None:
        if not self.config.state_file:
            return None
        p = Path(self.config.state_file).expanduser()
        if not p.is_absolute():
            p = self.root / p
        return p.resolve(strict=False)

    def _load_state(self) -> None:
        path = self._state_path()
        if not path or not path.exists():
            return
        obj = load_json(path, default={})
        if isinstance(obj, dict):
            self._paused_until = float(obj.get("paused_until") or 0.0)
            self._last_event = obj.get("last_event") if isinstance(obj.get("last_event"), dict) else None
            self._drive_snapshot = dict(obj.get("drive_snapshot") or {})

    def _save_state(self) -> None:
        path = self._state_path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        locked_atomic_write_json(path, {"paused_until": self._paused_until, "last_event": self._last_event, "drive_snapshot": self._drive_snapshot})


def _current_drive_snapshot() -> dict[str, str]:
    if platform.system().lower() != "windows":
        return {}
    out: dict[str, str] = {}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        if root.exists():
            out[f"{letter}:"] = str(root.resolve(strict=False))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCPGuardian Windows Event Guard")
    parser.add_argument("--config", help="JSON config with windows_event_guard section")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    rec = sub.add_parser("record")
    rec.add_argument("--event-type", required=True)
    rec.add_argument("--device-id", default="")
    args = parser.parse_args(argv)
    raw = load_json(args.config, default={}) if args.config else {}
    cfg = WindowsEventGuardConfig.from_dict(raw.get("windows_event_guard", raw if isinstance(raw, dict) else {}))
    guard = WindowsEventGuard(cfg)
    if args.cmd == "record":
        result = guard.record_device_change(event_type=args.event_type, device_id=args.device_id)
    else:
        result = guard.status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
