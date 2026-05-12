"""Generate Windows launcher scripts for MCPGuardian operations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..atomic_io import locked_atomic_write_json


def _ps_quote(value: str | Path) -> str:
    text = str(value).replace("'", "''")
    return f"'{text}'"


def write_windows_launchers(*, output_dir: str | Path, root: str | Path, python_exe: str, gateway_config: str | Path, http_config: str | Path | None = None) -> dict[str, Any]:
    out = Path(output_dir).expanduser().resolve(strict=False)
    out.mkdir(parents=True, exist_ok=True)
    root_path = Path(root).expanduser().resolve(strict=False)
    python = str(python_exe)
    gateway_cfg = Path(gateway_config).expanduser().resolve(strict=False)
    http_cfg = Path(http_config).expanduser().resolve(strict=False) if http_config else gateway_cfg

    scripts: dict[str, str] = {}
    scripts["run_diagnostics.ps1"] = f"""$ErrorActionPreference = 'Stop'\n$env:MCPGUARDIAN_ROOT = {_ps_quote(root_path)}\n$env:MCPGUARDIAN_CONFIG = {_ps_quote(gateway_cfg)}\n& {_ps_quote(python)} -m guardian.packaging.diagnostics --root $env:MCPGUARDIAN_ROOT --config-path $env:MCPGUARDIAN_CONFIG\nexit $LASTEXITCODE\n"""
    scripts["start_stdio_gateway.ps1"] = f"""$ErrorActionPreference = 'Stop'\n$env:MCPGUARDIAN_ROOT = {_ps_quote(root_path)}\n$env:MCPGUARDIAN_CONFIG = {_ps_quote(gateway_cfg)}\n$env:MCPGUARDIAN_ENABLE_RULE_MUTATION = '0'\n& {_ps_quote(python)} {_ps_quote(root_path / 'guardian_gateway.py')}\nexit $LASTEXITCODE\n"""
    scripts["start_http_gateway.ps1"] = f"""param(\n  [Parameter(Mandatory=$true)][string]$BearerToken,\n  [int]$Port = 8000\n)\n$ErrorActionPreference = 'Stop'\nif ($BearerToken.Length -lt 32) {{ throw 'BearerToken must be 32+ characters.' }}\n$env:MCPGUARDIAN_ROOT = {_ps_quote(root_path)}\n$env:MCPGUARDIAN_CONFIG = {_ps_quote(http_cfg)}\n$env:MCPGUARDIAN_HTTP_PORT = [string]$Port\n$env:MCPGUARDIAN_BEARER_TOKEN = $BearerToken\n$env:MCPGUARDIAN_ENABLE_RULE_MUTATION = '0'\n& {_ps_quote(python)} {_ps_quote(root_path / 'guardian_http_gateway.py')}\nexit $LASTEXITCODE\n"""
    scripts["migrate_claude_desktop.ps1"] = f"""param(\n  [string]$ClaudeConfig = "$env:APPDATA\\Claude\\claude_desktop_config.json",\n  [switch]$DryRun\n)\n$ErrorActionPreference = 'Stop'\n$argsList = @(\n  '-m','guardian.packaging.config_migration',\n  '--config-path', $ClaudeConfig,\n  '--python-exe', {_ps_quote(python)},\n  '--gateway-script', {_ps_quote(root_path / 'guardian_gateway.py')},\n  '--root', {_ps_quote(root_path)},\n  '--guardian-config', {_ps_quote(gateway_cfg)}\n)\nif ($DryRun) {{ $argsList += '--dry-run' }}\n& {_ps_quote(python)} @argsList\nexit $LASTEXITCODE\n"""
    scripts["rollback_claude_desktop.ps1"] = f"""param(\n  [string]$ClaudeConfig = "$env:APPDATA\\Claude\\claude_desktop_config.json",\n  [string]$BackupPath = ''\n)\n$ErrorActionPreference = 'Stop'\n$argsList = @('-m','guardian.packaging.config_migration','rollback','--config-path',$ClaudeConfig)\nif ($BackupPath) {{ $argsList += @('--backup-path', $BackupPath) }}\n& {_ps_quote(python)} @argsList\nexit $LASTEXITCODE\n"""
    scripts["register_http_gateway_task.ps1"] = f"""param(\n  [Parameter(Mandatory=$true)][string]$BearerToken,\n  [string]$TaskName = 'MCPGuardian HTTP Gateway',\n  [int]$Port = 8000\n)\n$ErrorActionPreference = 'Stop'\nif ($BearerToken.Length -lt 32) {{ throw 'BearerToken must be 32+ characters.' }}\n$script = {_ps_quote(out / 'start_http_gateway.ps1')}\n$argument = '-NoProfile -ExecutionPolicy Bypass -File ' + $script + ' -BearerToken ' + $BearerToken + ' -Port ' + $Port\n$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument\n$trigger = New-ScheduledTaskTrigger -AtLogOn\nRegister-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description 'Starts MCPGuardian HTTP Gateway at user logon.' -Force | Out-Null\nWrite-Output "Registered scheduled task: $TaskName"\n"""
    scripts["unregister_http_gateway_task.ps1"] = """param(\n  [string]$TaskName = 'MCPGuardian HTTP Gateway'\n)\n$ErrorActionPreference = 'Stop'\nUnregister-ScheduledTask -TaskName $TaskName -Confirm:$false\nWrite-Output "Unregistered scheduled task: $TaskName"\n"""

    written = []
    for name, content in scripts.items():
        path = out / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))

    manifest = {
        "ok": True,
        "launcher_dir": str(out),
        "root": str(root_path),
        "python_exe": python,
        "gateway_config": str(gateway_cfg),
        "http_config": str(http_cfg),
        "scripts": written,
    }
    locked_atomic_write_json(out / "launcher_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate MCPGuardian Windows launcher scripts")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--python-exe", required=True)
    parser.add_argument("--gateway-config", required=True)
    parser.add_argument("--http-config")
    args = parser.parse_args(argv)
    result = write_windows_launchers(output_dir=args.output_dir, root=args.root, python_exe=args.python_exe, gateway_config=args.gateway_config, http_config=args.http_config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
