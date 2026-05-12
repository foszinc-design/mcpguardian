"""FastMCP-facing tool implementations for MCPGuardian Phase 5A.

These functions are transport-agnostic and return stable JSON-safe envelopes.
`mcp_server.py` only decorates them as MCP tools. Keeping the business logic
here makes Phase 5A testable without requiring Claude Desktop or the MCP SDK.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .atomic_io import load_json, locked_atomic_write_json
from .log_analyzer import analyze_to_pending
from .path_policy import PathPolicy, PathPolicyError
from .preflight_gate import evaluate_gate, load_active_rules
from .rule_reviewer import approve_rule, list_rules, reject_rule
from .schemas import GateRequest, InputFile
from .structured_trace import RunContext, TraceWriter
from .validators.claim_manifest_validator import validate_claim_manifest as _validate_claim_manifest
from .validators.xlsx_validator import generate_xlsx_artifacts


MUTATION_ENV = "MCPGUARDIAN_ENABLE_RULE_MUTATION"


@dataclass(frozen=True)
class GuardianPaths:
    root: Path
    runs_dir: Path
    active_rules: Path
    pending_rules: Path
    rule_history: Path
    config_path: Path | None = None
    allowed_roots: tuple[Path, ...] = ()
    rule_mutation_enabled: bool = False

    @classmethod
    def load(cls, *, root: str | Path | None = None, config_path: str | Path | None = None) -> "GuardianPaths":
        root_path = Path(root or os.environ.get("MCPGUARDIAN_ROOT") or Path.cwd()).expanduser().resolve(strict=False)
        raw_config = config_path or os.environ.get("MCPGUARDIAN_CONFIG")
        cfg_path = Path(raw_config).expanduser().resolve(strict=False) if raw_config else root_path / "config" / "mcp_guardian_config.json"
        cfg = load_json(cfg_path, default={}) if cfg_path.exists() else {}
        paths = cfg.get("paths", {}) if isinstance(cfg, dict) else {}

        def _path(key: str, default: Path) -> Path:
            value = paths.get(key) if isinstance(paths, dict) else None
            p = Path(value).expanduser() if value else default
            if not p.is_absolute():
                p = root_path / p
            return p.resolve(strict=False)

        runs_dir = _path("runs_dir", root_path / "runs")
        active_rules = _path("active_rules", root_path / "config" / "active_rules.json")
        pending_rules = _path("pending_rules", root_path / "config" / "pending_rules.json")
        rule_history = _path("rule_history", root_path / "config" / "rule_history.jsonl")

        raw_allowed = cfg.get("allowed_roots", []) if isinstance(cfg, dict) else []
        allowed: list[Path] = [root_path, runs_dir, active_rules.parent, pending_rules.parent]
        for item in raw_allowed:
            p = Path(item).expanduser()
            if not p.is_absolute():
                p = root_path / p
            allowed.append(p.resolve(strict=False))

        env_mutation = os.environ.get(MUTATION_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}
        cfg_mutation = bool(cfg.get("enable_rule_mutation", False)) if isinstance(cfg, dict) else False

        return cls(
            root=root_path,
            runs_dir=runs_dir,
            active_rules=active_rules,
            pending_rules=pending_rules,
            rule_history=rule_history,
            config_path=cfg_path,
            allowed_roots=tuple(dict.fromkeys(allowed)),
            rule_mutation_enabled=env_mutation and cfg_mutation,
        )

    def policy(self) -> PathPolicy:
        return PathPolicy.from_roots(self.allowed_roots)


def ok_envelope(*, run_id: str | None = None, message: str = "ok", artifacts: list[str] | None = None, warnings: list[Any] | None = None, data: Any = None, **extra: Any) -> dict[str, Any]:
    out = {
        "ok": True,
        "message": message,
        "run_id": run_id,
        "artifacts": artifacts or [],
        "warnings": warnings or [],
        "errors": [],
    }
    if data is not None:
        out["data"] = data
    out.update(extra)
    return out


def error_envelope(error_code: str, message: str, *, run_id: str | None = None, errors: list[Any] | None = None, warnings: list[Any] | None = None, artifacts: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    out = {
        "ok": False,
        "error_code": error_code,
        "message": message,
        "run_id": run_id,
        "artifacts": artifacts or [],
        "warnings": warnings or [],
        "errors": errors or [],
    }
    out.update(extra)
    return out


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, PathPolicyError):
        return "PATH_POLICY_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "FILE_NOT_FOUND"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, ValueError):
        return "INVALID_ARGUMENT"
    return "INTERNAL_ERROR"


def _paths(root: str | Path | None = None, config_path: str | Path | None = None) -> GuardianPaths:
    return GuardianPaths.load(root=root, config_path=config_path)


def _new_run(paths: GuardianPaths, *, run_id: str | None = None) -> RunContext:
    paths.runs_dir.mkdir(parents=True, exist_ok=True)
    if run_id:
        run_dir = paths.runs_dir / run_id
        if run_dir.exists():
            return _ExistingRunContext(paths.runs_dir, run_id)
    return RunContext(paths.runs_dir, run_id=run_id)


class _ExistingRunContext(RunContext):
    def __init__(self, base_runs_dir: str | Path, run_id: str) -> None:
        self.run_id = run_id
        self.run_dir = Path(base_runs_dir) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.run_dir / "trace.jsonl"


def _writer(run: RunContext) -> TraceWriter:
    return run.writer()


def _safe_emit(writer: TraceWriter, event_type: str, **payload: Any) -> None:
    try:
        writer.emit(event_type, **payload)
    except Exception:
        # Tool results must not fail merely because trace append failed. The
        # error is surfaced only when the primary operation itself fails.
        pass


def _artifact_paths(run_dir: Path, names: list[str]) -> list[str]:
    return [str(run_dir / name) for name in names]


def mcpguardian_preflight(
    *,
    task_type: str,
    requested_action: str = "",
    input_paths: list[str] | None = None,
    existing_artifacts: list[str] | None = None,
    run_id: str | None = None,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    paths = _paths(root=root, config_path=config_path)
    run: RunContext | None = None
    try:
        policy = paths.policy()
        safe_inputs = [policy.resolve_allowed(item, must_exist=False) for item in (input_paths or [])]
        run = _new_run(paths, run_id=run_id)
        writer = _writer(run)
        input_strings = [str(item) for item in safe_inputs]
        writer.run_started(task_type=task_type, requested_action=requested_action, input_files=input_strings)
        run.write_input_manifest(input_strings)
        rules = load_active_rules(paths.active_rules)
        request = GateRequest(
            task_type=task_type,
            requested_action=requested_action,
            input_files=[InputFile.from_path(path) for path in safe_inputs],
            run_dir=str(run.run_dir),
            existing_artifacts=existing_artifacts or [],
        )
        decision = evaluate_gate(request, rules)
        decision_obj = decision.to_dict()
        writer.preflight_evaluated(decision_obj)
        writer.run_finished(status="ok", summary=f"preflight decision={decision.decision}")
        return ok_envelope(
            run_id=run.run_id,
            message="preflight evaluated",
            decision=decision.decision,
            risk_level=decision.risk_level,
            required_artifacts=decision.required_artifacts,
            matched_rules=decision.matched_rules,
            warnings=decision.messages,
            artifacts=_artifact_paths(run.run_dir, ["trace.jsonl", "input_manifest.json"]),
            data=decision_obj,
        )
    except Exception as exc:
        if run is not None:
            _safe_emit(_writer(run), "tool_failed", tool="mcpguardian_preflight", error=str(exc), error_code=_classify_error(exc))
        return error_envelope(_classify_error(exc), str(exc), run_id=run.run_id if run else None)


def mcpguardian_validate_xlsx(
    *,
    input_path: str,
    analyzed_sheets: list[str] | None = None,
    assume_full_analysis: bool = False,
    run_id: str | None = None,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    paths = _paths(root=root, config_path=config_path)
    run: RunContext | None = None
    try:
        safe_input = paths.policy().resolve_allowed(input_path, must_exist=True)
        run = _new_run(paths, run_id=run_id)
        writer = _writer(run)
        writer.run_started(task_type="xlsx_validation", requested_action="validate_xlsx", input_files=[str(safe_input)])
        run.write_input_manifest([str(safe_input)])
        outputs = generate_xlsx_artifacts(
            safe_input,
            run.run_dir,
            analyzed_sheets=analyzed_sheets or [],
            assume_full_analysis=assume_full_analysis,
        )
        for artifact_type, path in outputs.items():
            writer.artifact_registered(artifact_name=Path(path).name, path=str(path), artifact_type=artifact_type)
        writer.run_finished(status="ok", summary="xlsx validation artifacts generated")
        return ok_envelope(
            run_id=run.run_id,
            message="xlsx validation artifacts generated",
            artifacts=[str(path) for path in outputs.values()],
            data={"produced_artifacts": {key: str(value) for key, value in outputs.items()}},
        )
    except Exception as exc:
        if run is not None:
            _safe_emit(_writer(run), "tool_failed", tool="mcpguardian_validate_xlsx", error=str(exc), error_code=_classify_error(exc))
        return error_envelope(_classify_error(exc), str(exc), run_id=run.run_id if run else None)


def mcpguardian_validate_claim_manifest(
    *,
    manifest_path: str,
    run_id: str | None = None,
    run_dir: str | None = None,
    output_document: str | None = None,
    strict_output_coverage: bool = False,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    paths = _paths(root=root, config_path=config_path)
    run: RunContext | None = None
    try:
        policy = paths.policy()
        safe_manifest = policy.resolve_allowed(manifest_path, must_exist=True)
        if run_dir:
            safe_run_dir = policy.resolve_allowed(run_dir, must_exist=False)
            safe_run_dir.mkdir(parents=True, exist_ok=True)
            run = _ExistingRunContext(safe_run_dir.parent, safe_run_dir.name)
        else:
            run = _new_run(paths, run_id=run_id)
            safe_run_dir = run.run_dir
        safe_output_document = policy.resolve_allowed(output_document, must_exist=True) if output_document else None
        writer = _writer(run)
        writer.run_started(task_type="claim_manifest_validation", requested_action="validate_claim_manifest", input_files=[str(safe_manifest)])
        result = _validate_claim_manifest(
            safe_manifest,
            run_dir=safe_run_dir,
            output_document=safe_output_document,
            strict_output_coverage=strict_output_coverage,
        )
        result_path = safe_run_dir / "claim_manifest_validator_result.json"
        locked_atomic_write_json(result_path, result)
        writer.artifact_registered(
            artifact_name=result_path.name,
            path=str(result_path),
            artifact_type="claim_manifest_validator_result",
        )
        writer.run_finished(status="ok" if result.get("ok") else "failed", summary="claim manifest validation completed")
        if result.get("ok") is not True:
            return error_envelope(
                "VALIDATION_FAILED",
                "claim manifest validation failed",
                run_id=run.run_id,
                artifacts=[str(result_path)],
                warnings=result.get("warnings") or [],
                errors=result.get("errors") or [],
                data=result,
            )
        return ok_envelope(
            run_id=run.run_id,
            message="claim manifest validation passed",
            artifacts=[str(result_path)],
            warnings=result.get("warnings") or [],
            data=result,
        )
    except Exception as exc:
        if run is not None:
            _safe_emit(_writer(run), "tool_failed", tool="mcpguardian_validate_claim_manifest", error=str(exc), error_code=_classify_error(exc))
        return error_envelope(_classify_error(exc), str(exc), run_id=run.run_id if run else None)


def mcpguardian_analyze_runs(
    *,
    min_occurrences: int = 2,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    try:
        paths = _paths(root=root, config_path=config_path)
        result = analyze_to_pending(
            runs_dir=paths.runs_dir,
            pending_path=paths.pending_rules,
            active_rules_path=paths.active_rules,
            min_occurrences=min_occurrences,
        )
        return ok_envelope(message="runs analyzed; pending rules updated", artifacts=[str(paths.pending_rules)], data=result)
    except Exception as exc:
        return error_envelope(_classify_error(exc), str(exc))


def mcpguardian_list_pending_rules(
    *,
    status: str | None = None,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    try:
        paths = _paths(root=root, config_path=config_path)
        rules = list_rules(pending_path=paths.pending_rules, status=status)
        return ok_envelope(message="pending rules listed", artifacts=[str(paths.pending_rules)], data={"rules": rules, "count": len(rules)})
    except Exception as exc:
        return error_envelope(_classify_error(exc), str(exc))


def _mutation_guard(paths: GuardianPaths) -> None:
    if not paths.rule_mutation_enabled:
        raise PermissionError(
            "Rule mutation is disabled. Set MCPGUARDIAN_ENABLE_RULE_MUTATION=1 and enable_rule_mutation=true in mcp_guardian_config.json."
        )


def mcpguardian_approve_rule(
    *,
    rule_id: str,
    note: str | None = None,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    try:
        paths = _paths(root=root, config_path=config_path)
        _mutation_guard(paths)
        result = approve_rule(
            rule_id=rule_id,
            pending_path=paths.pending_rules,
            active_path=paths.active_rules,
            history_path=paths.rule_history,
            note=note,
        )
        return ok_envelope(
            message="rule approved",
            artifacts=[str(paths.active_rules), str(paths.pending_rules), str(paths.rule_history)],
            data=result,
        )
    except Exception as exc:
        return error_envelope(_classify_error(exc), str(exc))


def mcpguardian_reject_rule(
    *,
    rule_id: str,
    reason: str,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    try:
        paths = _paths(root=root, config_path=config_path)
        _mutation_guard(paths)
        result = reject_rule(rule_id=rule_id, pending_path=paths.pending_rules, history_path=paths.rule_history, reason=reason)
        return ok_envelope(message="rule rejected", artifacts=[str(paths.pending_rules), str(paths.rule_history)], data=result)
    except Exception as exc:
        return error_envelope(_classify_error(exc), str(exc))


def mcpguardian_get_run_summary(
    *,
    run_id: str,
    root: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    try:
        paths = _paths(root=root, config_path=config_path)
        run_dir = paths.runs_dir / run_id
        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(str(run_dir))
        paths.policy().resolve_allowed(run_dir, must_exist=True)
        artifacts = sorted(str(path) for path in run_dir.iterdir() if path.is_file())
        trace_path = run_dir / "trace.jsonl"
        trace_events = 0
        if trace_path.exists():
            trace_events = sum(1 for _ in trace_path.open("r", encoding="utf-8"))
        summary = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "artifacts": artifacts,
            "trace_events": trace_events,
        }
        return ok_envelope(run_id=run_id, message="run summary loaded", artifacts=artifacts, data=summary)
    except Exception as exc:
        return error_envelope(_classify_error(exc), str(exc), run_id=run_id)
