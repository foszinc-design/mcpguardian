"""Claude Desktop config migration helpers for MCPGuardian Phase 8A.

The migration is deliberately conservative:
- create a timestamped backup before writing;
- preserve unrelated MCP servers;
- remove only known local backend servers that MCPGuardian can replace;
- never mutate files during dry-run.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..atomic_io import locked_atomic_write_json, load_json

REPLACED_BACKEND_NAMES = {"desktop-commander", "windows-mcp", "filesystem"}


def build_guardian_server_config(*, python_exe: str, gateway_script: str | Path, root: str | Path, guardian_config: str | Path) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": str(python_exe),
        "args": [str(gateway_script)],
        "env": {
            "MCPGUARDIAN_ROOT": str(root),
            "MCPGUARDIAN_CONFIG": str(guardian_config),
            "MCPGUARDIAN_ENABLE_RULE_MUTATION": "0",
        },
    }


def migrate_claude_desktop_config(
    *,
    config_path: str | Path,
    python_exe: str,
    gateway_script: str | Path,
    root: str | Path,
    guardian_config: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve(strict=False)
    original = load_json(path, default={}) if path.exists() else {}
    if not isinstance(original, dict):
        raise ValueError("Claude Desktop config must be a JSON object")
    migrated = json.loads(json.dumps(original))
    servers = migrated.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be a JSON object")

    removed = []
    for name in sorted(REPLACED_BACKEND_NAMES):
        if name in servers:
            removed.append(name)
            servers.pop(name, None)
    servers["mcp-guardian"] = build_guardian_server_config(
        python_exe=python_exe,
        gateway_script=gateway_script,
        root=root,
        guardian_config=guardian_config,
    )

    backup_path = None
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = path.with_suffix(path.suffix + f".bak_{stamp}")
            shutil.copy2(path, backup_path)
        locked_atomic_write_json(path, migrated)

    return {
        "ok": True,
        "config_path": str(path),
        "dry_run": dry_run,
        "removed_servers": removed,
        "added_server": "mcp-guardian",
        "backup_path": str(backup_path) if backup_path else None,
        "migrated": migrated,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate Claude Desktop MCP config to MCPGuardian")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--python-exe", required=True)
    parser.add_argument("--gateway-script", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--guardian-config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = migrate_claude_desktop_config(
        config_path=args.config_path,
        python_exe=args.python_exe,
        gateway_script=args.gateway_script,
        root=args.root,
        guardian_config=args.guardian_config,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
