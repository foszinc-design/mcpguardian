"""Build and validate a lightweight MCPGuardian release manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..atomic_io import sha256_file

REQUIRED_FILES = [
    "guardian_gateway.py",
    "guardian_http_gateway.py",
    "requirements.txt",
    "config/active_rules.json",
    "config/gateway_config.phase9.example.json",
    "README_PHASE9.md",
]


def build_release_manifest(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve(strict=False)
    files = []
    missing = []
    for rel in REQUIRED_FILES:
        path = root_path / rel
        if path.exists():
            files.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
        else:
            missing.append(rel)
    return {"ok": not missing, "root": str(root_path), "required_files": files, "missing": missing}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build MCPGuardian release manifest")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    manifest = build_release_manifest(args.root)
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if manifest["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
