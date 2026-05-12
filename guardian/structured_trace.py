"""Run directory and structured trace support for MCPGuardian Phase 1."""
from __future__ import annotations

import secrets
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .atomic_io import append_jsonl, locked_atomic_write_json, sha256_file
from .schemas import InputFile, TraceEvent


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{secrets.token_hex(3)}"


class RunContext:
    def __init__(self, base_runs_dir: str | Path, run_id: str | None = None) -> None:
        self.run_id = run_id or new_run_id()
        self.run_dir = Path(base_runs_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.trace_path = self.run_dir / "trace.jsonl"

    def writer(self) -> "TraceWriter":
        return TraceWriter(self.run_id, self.trace_path)

    def write_input_manifest(self, input_paths: list[str | Path]) -> Path:
        files: list[dict[str, Any]] = []
        for raw in input_paths:
            path = Path(raw)
            digest = sha256_file(path) if path.exists() and path.is_file() else None
            files.append(asdict(InputFile.from_path(path, sha256=digest)))
        manifest = {"run_id": self.run_id, "files": files}
        output = self.run_dir / "input_manifest.json"
        locked_atomic_write_json(output, manifest)
        return output


class TraceWriter:
    def __init__(self, run_id: str, trace_path: str | Path) -> None:
        self.run_id = run_id
        self.trace_path = Path(trace_path)

    def emit(self, event_type: str, **payload: Any) -> None:
        event = TraceEvent.create(event_type, self.run_id, **payload)
        append_jsonl(self.trace_path, event.to_dict())

    def run_started(self, *, task_type: str, requested_action: str, input_files: list[str]) -> None:
        self.emit("run_started", task_type=task_type, requested_action=requested_action, input_files=input_files)

    def preflight_evaluated(self, decision: dict[str, Any]) -> None:
        self.emit("preflight_evaluated", **decision)

    def artifact_registered(self, *, artifact_name: str, path: str, artifact_type: str) -> None:
        self.emit("artifact_registered", artifact_name=artifact_name, path=path, artifact_type=artifact_type)

    def run_finished(self, *, status: str, summary: str | None = None) -> None:
        self.emit("run_finished", status=status, summary=summary)
