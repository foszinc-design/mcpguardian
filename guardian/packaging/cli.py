"""Unified operator CLI for MCPGuardian deployment tasks."""
from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

from .config_migration import migrate_claude_desktop_config, rollback_claude_desktop_config, print_guardian_config, default_claude_desktop_config_path
from .diagnostics import run_diagnostics
from .launchers import write_windows_launchers
from .release_manifest import build_release_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcpguardianctl", description="MCPGuardian operator CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    doctor = sub.add_parser("doctor", help="Run diagnostics")
    doctor.add_argument("--root")
    doctor.add_argument("--config-path")
    doctor.add_argument("--http", action="store_true")

    token = sub.add_parser("make-token", help="Generate a bearer token")
    token.add_argument("--bytes", type=int, default=32)

    migrate = sub.add_parser("migrate-claude", help="Migrate Claude Desktop config")
    migrate.add_argument("--config-path", default=str(default_claude_desktop_config_path()))
    migrate.add_argument("--python-exe", required=True)
    migrate.add_argument("--gateway-script", required=True)
    migrate.add_argument("--root", required=True)
    migrate.add_argument("--guardian-config", required=True)
    migrate.add_argument("--dry-run", action="store_true")

    rollback = sub.add_parser("rollback-claude", help="Rollback Claude Desktop config")
    rollback.add_argument("--config-path", default=str(default_claude_desktop_config_path()))
    rollback.add_argument("--backup-path")
    rollback.add_argument("--dry-run", action="store_true")

    print_cfg = sub.add_parser("print-claude-config", help="Print only the mcp-guardian config block")
    print_cfg.add_argument("--python-exe", required=True)
    print_cfg.add_argument("--gateway-script", required=True)
    print_cfg.add_argument("--root", required=True)
    print_cfg.add_argument("--guardian-config", required=True)

    launchers = sub.add_parser("write-launchers", help="Generate Windows PowerShell launcher scripts")
    launchers.add_argument("--output-dir", required=True)
    launchers.add_argument("--root", required=True)
    launchers.add_argument("--python-exe", required=True)
    launchers.add_argument("--gateway-config", required=True)
    launchers.add_argument("--http-config")

    manifest = sub.add_parser("release-manifest", help="Validate release tree")
    manifest.add_argument("--root", default=".")

    args = parser.parse_args(argv)
    if args.cmd == "doctor":
        result = run_diagnostics(root=args.root, config_path=args.config_path, http=args.http)
        code = 0 if result.get("ok") else 2
    elif args.cmd == "make-token":
        if args.bytes < 24:
            parser.error("--bytes must be >= 24")
        result = {"ok": True, "token": secrets.token_urlsafe(args.bytes)}
        code = 0
    elif args.cmd == "migrate-claude":
        result = migrate_claude_desktop_config(config_path=args.config_path, python_exe=args.python_exe, gateway_script=args.gateway_script, root=args.root, guardian_config=args.guardian_config, dry_run=args.dry_run)
        code = 0
    elif args.cmd == "rollback-claude":
        result = rollback_claude_desktop_config(config_path=args.config_path, backup_path=args.backup_path, dry_run=args.dry_run)
        code = 0
    elif args.cmd == "print-claude-config":
        result = print_guardian_config(python_exe=args.python_exe, gateway_script=args.gateway_script, root=args.root, guardian_config=args.guardian_config)
        code = 0
    elif args.cmd == "write-launchers":
        result = write_windows_launchers(output_dir=args.output_dir, root=args.root, python_exe=args.python_exe, gateway_config=args.gateway_config, http_config=args.http_config)
        code = 0
    elif args.cmd == "release-manifest":
        result = build_release_manifest(args.root)
        code = 0 if result.get("ok") else 2
    else:  # pragma: no cover
        parser.error("unknown command")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
