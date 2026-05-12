"""Operational diagnostics for MCPGuardian Phase 8A."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..atomic_io import load_json
from ..mcp_tools import GuardianPaths
from ..path_policy import PathPolicyError
from ..preflight_gate import load_active_rules


def _check(name: str, ok: bool, *, severity: str = "error", message: str = "", data: Any = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "severity": severity, "message": message, "data": data}


def run_diagnostics(*, root: str | Path | None = None, config_path: str | Path | None = None, http: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        paths = GuardianPaths.load(root=root, config_path=config_path)
        checks.append(_check("paths.load", True, severity="info", data={"root": str(paths.root), "config": str(paths.config_path)}))
    except Exception as exc:
        return {"ok": False, "checks": [_check("paths.load", False, message=str(exc))]}

    checks.append(_check("python.version", sys.version_info >= (3, 11), message=sys.version.split()[0], data={"version": sys.version}))
    checks.append(_check("root.exists", paths.root.exists(), message=str(paths.root)))
    checks.append(_check("runs_dir.writable", _writable_dir(paths.runs_dir), message=str(paths.runs_dir)))
    checks.append(_check("active_rules.exists", paths.active_rules.exists(), message=str(paths.active_rules)))
    try:
        rules = load_active_rules(paths.active_rules)
        checks.append(_check("active_rules.load", True, severity="info", data={"count": len(rules)}))
    except Exception as exc:
        checks.append(_check("active_rules.load", False, message=str(exc)))

    try:
        policy = paths.policy()
        checks.append(_check("path_policy.load", True, severity="info", data={"allowed_roots": [str(p) for p in policy.allowed_roots]}))
    except PathPolicyError as exc:
        checks.append(_check("path_policy.load", False, message=str(exc)))

    if paths.config_path and paths.config_path.exists():
        cfg = load_json(paths.config_path, default={})
        checks.append(_check("config.json", isinstance(cfg, dict), severity="info", message=str(paths.config_path)))
        if isinstance(cfg, dict):
            backends = cfg.get("backends", {})
            checks.append(_check("backends.configured", isinstance(backends, dict), severity="warning", data={"count": len(backends) if isinstance(backends, dict) else 0}))

    if http:
        token = os.environ.get("MCPGUARDIAN_BEARER_TOKEN", "")
        checks.append(_check("http.bearer_token", len(token) >= 32, message="MCPGUARDIAN_BEARER_TOKEN must be 32+ chars for HTTP exposure"))

    failed = [item for item in checks if not item["ok"] and item.get("severity") == "error"]
    warnings = [item for item in checks if not item["ok"] and item.get("severity") == "warning"]
    return {"ok": not failed, "failed": len(failed), "warnings": len(warnings), "checks": checks}


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".mcpguardian_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run MCPGuardian diagnostics")
    parser.add_argument("--root")
    parser.add_argument("--config-path")
    parser.add_argument("--http", action="store_true")
    args = parser.parse_args(argv)
    result = run_diagnostics(root=args.root, config_path=args.config_path, http=args.http)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
