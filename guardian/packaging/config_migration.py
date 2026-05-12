"""Claude Desktop config migration helpers for MCPGuardian.

The migration is deliberately conservative:
- create a timestamped backup before writing;
- preserve unrelated MCP servers;
- remove only known local backend servers that MCPGuardian can replace;
- never mutate files during dry-run;
- support explicit rollback to the latest backup.
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


def default_claude_desktop_config_path() -> Path:
    """Return the conventional Claude Desktop config path for the current OS."""
    import os
    import sys

    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


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


def list_claude_config_backups(config_path: str | Path) -> list[Path]:
    """List timestamped backups created by migrate_claude_desktop_config."""
    path = Path(config_path).expanduser().resolve(strict=False)
    pattern = path.name + ".bak_*"
    return sorted(path.parent.glob(pattern), key=lambda p: p.name)


def rollback_claude_desktop_config(*, config_path: str | Path, backup_path: str | Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Restore a Claude Desktop config backup.

    If backup_path is omitted, restore the newest backup matching
    claude_desktop_config.json.bak_YYYYMMDD_HHMMSS.
    """
    target = Path(config_path).expanduser().resolve(strict=False)
    if backup_path is None:
        backups = list_claude_config_backups(target)
        if not backups:
            raise FileNotFoundError(f"No backups found for {target}")
        source = backups[-1]
    else:
        source = Path(backup_path).expanduser().resolve(strict=False)
    if not source.exists():
        raise FileNotFoundError(str(source))

    restored = load_json(source, default=None)
    if not isinstance(restored, dict):
        raise ValueError("Backup must be a JSON object")

    pre_rollback_backup = None
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pre_rollback_backup = target.with_suffix(target.suffix + f".pre_rollback_{stamp}")
            shutil.copy2(target, pre_rollback_backup)
        locked_atomic_write_json(target, restored)

    return {
        "ok": True,
        "dry_run": dry_run,
        "config_path": str(target),
        "restored_from": str(source),
        "pre_rollback_backup": str(pre_rollback_backup) if pre_rollback_backup else None,
    }


def print_guardian_config(*, python_exe: str, gateway_script: str | Path, root: str | Path, guardian_config: str | Path) -> dict[str, Any]:
    return {"mcpServers": {"mcp-guardian": build_guardian_server_config(python_exe=python_exe, gateway_script=gateway_script, root=root, guardian_config=guardian_config)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate or rollback Claude Desktop MCP config for MCPGuardian")
    sub = parser.add_subparsers(dest="cmd")

    migrate = sub.add_parser("migrate", help="Migrate Claude Desktop config to MCPGuardian")
    migrate.add_argument("--config-path", required=True)
    migrate.add_argument("--python-exe", required=True)
    migrate.add_argument("--gateway-script", required=True)
    migrate.add_argument("--root", required=True)
    migrate.add_argument("--guardian-config", required=True)
    migrate.add_argument("--dry-run", action="store_true")

    rollback = sub.add_parser("rollback", help="Restore a migration backup")
    rollback.add_argument("--config-path", required=True)
    rollback.add_argument("--backup-path")
    rollback.add_argument("--dry-run", action="store_true")

    backups = sub.add_parser("list-backups", help="List migration backups")
    backups.add_argument("--config-path", required=True)

    # Backward-compatible flat args used by Phase 8A docs/tests.
    parser.add_argument("--config-path")
    parser.add_argument("--python-exe")
    parser.add_argument("--gateway-script")
    parser.add_argument("--root")
    parser.add_argument("--guardian-config")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "rollback":
        result = rollback_claude_desktop_config(config_path=args.config_path, backup_path=args.backup_path, dry_run=args.dry_run)
    elif args.cmd == "list-backups":
        result = {"ok": True, "backups": [str(p) for p in list_claude_config_backups(args.config_path)]}
    else:
        missing = [name for name in ["config_path", "python_exe", "gateway_script", "root", "guardian_config"] if not getattr(args, name, None)]
        if missing:
            parser.error("missing required arguments: " + ", ".join("--" + m.replace("_", "-") for m in missing))
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
