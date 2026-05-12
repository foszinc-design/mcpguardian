"""Pending-rule candidate generator for MCPGuardian Phase 3.

The analyzer is intentionally conservative: it never writes active_rules.json.
It reads structured run evidence and writes review-required candidates to
pending_rules.json only.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .atomic_io import load_json, locked_atomic_write_json
from .schemas import utc_now_iso

ARTIFACT_SCOPE_HINTS = {
    "sheet_inventory.json": "xlsx",
    "row_count_summary.json": "xlsx",
    "coverage_report.json": "xlsx",
    "claim_manifest.json": "markdown",
    "change_manifest.json": "code",
}

EXT_SCOPE_HINTS = {
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".csv": "csv",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".md": "markdown",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                events.append(
                    {
                        "event_type": "trace_parse_error",
                        "line_number": line_number,
                        "raw": raw[:500],
                    }
                )
                continue
            if isinstance(obj, dict):
                events.append(obj)
    return events


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return slug or "rule"


def _artifact_stem(artifact_name: str) -> str:
    return _safe_slug(Path(artifact_name).stem)


def _extract_extensions(run_started: dict[str, Any] | None) -> list[str]:
    if not run_started:
        return []
    raw_files = run_started.get("input_files") or []
    extensions: set[str] = set()
    for item in raw_files:
        ext = Path(str(item)).suffix.lower()
        if ext:
            extensions.add(ext)
    return sorted(extensions)


def _infer_scope(artifact_name: str | None, extensions: Iterable[str], task_type: str | None = None) -> str:
    if artifact_name and artifact_name in ARTIFACT_SCOPE_HINTS:
        return ARTIFACT_SCOPE_HINTS[artifact_name]
    for ext in extensions:
        if ext in EXT_SCOPE_HINTS:
            return EXT_SCOPE_HINTS[ext]
    if task_type:
        if "xlsx" in task_type or "spreadsheet" in task_type:
            return "xlsx"
        if "code" in task_type or "bugfix" in task_type or "refactor" in task_type:
            return "code"
        if "report" in task_type or "document" in task_type:
            return "markdown"
    return "general"


def _load_rules(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    raw = load_json(path, default={"rules": []})
    if isinstance(raw, dict):
        return list(raw.get("rules", []))
    if isinstance(raw, list):
        return raw
    return []


def _active_rule_keys(active_rules: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for rule in active_rules:
        enforcement = str(rule.get("enforcement", ""))
        for task_type in rule.get("task_types", []) or []:
            for artifact in rule.get("required_artifacts", []) or []:
                keys.add((str(task_type), str(artifact), enforcement))
    return keys


def collect_run_observations(runs_dir: str | Path) -> list[dict[str, Any]]:
    """Collect structured observations from run directories.

    Observations are not rules. They are evidence items that may later become
    proposed rules after grouping and thresholding.
    """
    root = Path(runs_dir)
    observations: list[dict[str, Any]] = []
    if not root.exists():
        return observations

    for run_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        trace_path = run_dir / "trace.jsonl"
        events = _read_jsonl(trace_path)
        run_started = next((event for event in events if event.get("event_type") == "run_started"), None)
        task_type = str((run_started or {}).get("task_type") or "unknown")
        requested_action = str((run_started or {}).get("requested_action") or "")
        extensions = _extract_extensions(run_started)

        for event in events:
            if event.get("event_type") != "preflight_evaluated":
                continue
            decision = str(event.get("decision") or "")
            required = event.get("required_artifacts") or []
            if decision not in {"require_artifact", "postcheck_required"}:
                continue
            for artifact in required:
                artifact_name = str(artifact)
                observations.append(
                    {
                        "kind": "missing_required_artifact",
                        "run_id": run_dir.name,
                        "task_type": task_type,
                        "requested_action": requested_action,
                        "decision": decision,
                        "artifact": artifact_name,
                        "extensions": extensions,
                        "scope": _infer_scope(artifact_name, extensions, task_type),
                        "evidence_ref": str(trace_path),
                    }
                )

        validator_paths = sorted({run_dir / "validator_result.json", *run_dir.glob("*_validator_result.json")})
        for validator_path in validator_paths:
            if not validator_path.exists():
                continue
            try:
                validator = load_json(validator_path)
            except Exception as exc:
                observations.append(
                    {
                        "kind": "validator_result_unreadable",
                        "run_id": run_dir.name,
                        "task_type": task_type,
                        "extensions": extensions,
                        "scope": _infer_scope(None, extensions, task_type),
                        "message": str(exc),
                        "evidence_ref": str(validator_path),
                    }
                )
                continue
            validator_name = str(validator.get("validator") or validator_path.stem)
            for level in ("errors", "warnings"):
                for message in validator.get(level, []) or []:
                    observations.append(
                        {
                            "kind": "validator_message",
                            "level": level[:-1],
                            "run_id": run_dir.name,
                            "task_type": task_type,
                            "extensions": extensions,
                            "scope": _infer_scope(None, extensions, task_type),
                            "validator": validator_name,
                            "message": str(message),
                            "evidence_ref": str(validator_path),
                        }
                    )
    return observations


def _candidate_for_missing_artifact(
    *,
    key: tuple[str, str, str, tuple[str, ...]],
    items: list[dict[str, Any]],
    active_keys: set[tuple[str, str, str]],
) -> dict[str, Any] | None:
    task_type, artifact_name, decision, ext_tuple = key
    proposed_enforcement = "require_artifact" if decision == "require_artifact" else "postcheck_required"
    if (task_type, artifact_name, proposed_enforcement) in active_keys:
        return None

    scope = items[0].get("scope") or _infer_scope(artifact_name, ext_tuple, task_type)
    artifact_slug = _artifact_stem(artifact_name)
    target_rule_id = f"{scope}.require_{artifact_slug}.v1"
    candidate_id = f"candidate.{target_rule_id}"
    evidence_refs = sorted({str(item["evidence_ref"]) for item in items})
    condition: dict[str, Any] = {}
    if ext_tuple:
        condition["input_file_ext"] = list(ext_tuple)

    severity = "high" if proposed_enforcement == "require_artifact" else "medium"
    return {
        "id": candidate_id,
        "target_rule_id": target_rule_id,
        "status": "proposed",
        "review_required": True,
        "scope": scope,
        "task_types": [task_type],
        "proposed_severity": severity,
        "proposed_enforcement": proposed_enforcement,
        "proposed_condition": condition,
        "proposed_required_artifacts": [artifact_name],
        "proposed_message": f"{task_type} 작업은 {artifact_name} 없이는 통과시키지 않는다.",
        "confidence": min(0.95, 0.55 + 0.1 * len(items)),
        "evidence_refs": evidence_refs,
        "observed_failure": f"{len(items)} run(s) required missing artifact {artifact_name} during {decision}.",
        "false_positive_risk": "medium" if ext_tuple else "high",
        "created_at": utc_now_iso(),
        "source": "log_analyzer.missing_required_artifact",
    }


def _candidate_for_validator_message(
    *,
    key: tuple[str, str, str, tuple[str, ...]],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    task_type, level, message_slug, ext_tuple = key
    first = items[0]
    scope = first.get("scope") or _infer_scope(None, ext_tuple, task_type)
    severity = "high" if level == "error" else "medium"
    target_rule_id = f"{scope}.warn_validator_{message_slug}.v1"
    condition: dict[str, Any] = {}
    if ext_tuple:
        condition["input_file_ext"] = list(ext_tuple)
    return {
        "id": f"candidate.{target_rule_id}",
        "target_rule_id": target_rule_id,
        "status": "proposed",
        "review_required": True,
        "scope": scope,
        "task_types": [task_type],
        "proposed_severity": severity,
        "proposed_enforcement": "warn",
        "proposed_condition": condition,
        "proposed_required_artifacts": [],
        "proposed_message": f"반복 validator {level}: {first.get('message')}",
        "confidence": min(0.9, 0.5 + 0.1 * len(items)),
        "evidence_refs": sorted({str(item["evidence_ref"]) for item in items}),
        "observed_failure": f"{len(items)} run(s) emitted validator {level}: {first.get('message')}",
        "false_positive_risk": "high",
        "created_at": utc_now_iso(),
        "source": "log_analyzer.validator_message",
    }


def generate_rule_candidates(
    observations: list[dict[str, Any]],
    *,
    active_rules: list[dict[str, Any]] | None = None,
    min_occurrences: int = 2,
) -> list[dict[str, Any]]:
    active_keys = _active_rule_keys(active_rules or [])
    candidates: list[dict[str, Any]] = []

    missing_groups: dict[tuple[str, str, str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    validator_groups: dict[tuple[str, str, str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)

    for obs in observations:
        if obs.get("kind") == "missing_required_artifact":
            key = (
                str(obs.get("task_type") or "unknown"),
                str(obs.get("artifact")),
                str(obs.get("decision")),
                tuple(obs.get("extensions") or []),
            )
            missing_groups[key].append(obs)
        elif obs.get("kind") == "validator_message":
            key = (
                str(obs.get("task_type") or "unknown"),
                str(obs.get("level") or "warning"),
                _safe_slug(str(obs.get("message") or "message"))[:60],
                tuple(obs.get("extensions") or []),
            )
            validator_groups[key].append(obs)

    for key, items in sorted(missing_groups.items()):
        if len(items) < min_occurrences:
            continue
        candidate = _candidate_for_missing_artifact(key=key, items=items, active_keys=active_keys)
        if candidate is not None:
            candidates.append(candidate)

    for key, items in sorted(validator_groups.items()):
        if len(items) < min_occurrences:
            continue
        candidates.append(_candidate_for_validator_message(key=key, items=items))

    return candidates


def merge_pending_rules(existing: list[dict[str, Any]], new_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge by candidate id without resurrecting rejected or staged rules."""
    by_id: dict[str, dict[str, Any]] = {str(rule.get("id")): dict(rule) for rule in existing if rule.get("id")}
    for candidate in new_candidates:
        candidate_id = str(candidate["id"])
        old = by_id.get(candidate_id)
        if old and old.get("status") in {"rejected", "staged"}:
            continue
        if old:
            evidence = sorted(set(old.get("evidence_refs", [])) | set(candidate.get("evidence_refs", [])))
            old.update(candidate)
            old["evidence_refs"] = evidence
            old["updated_at"] = utc_now_iso()
            by_id[candidate_id] = old
        else:
            by_id[candidate_id] = dict(candidate)
    return sorted(by_id.values(), key=lambda item: str(item.get("id")))


def analyze_to_pending(
    *,
    runs_dir: str | Path,
    pending_path: str | Path,
    active_rules_path: str | Path | None = None,
    min_occurrences: int = 2,
) -> dict[str, Any]:
    observations = collect_run_observations(runs_dir)
    active_rules = _load_rules(active_rules_path)
    candidates = generate_rule_candidates(observations, active_rules=active_rules, min_occurrences=min_occurrences)
    pending_obj = load_json(pending_path, default={"rules": []})
    existing = pending_obj.get("rules", []) if isinstance(pending_obj, dict) else []
    merged = merge_pending_rules(existing, candidates)
    output = {"rules": merged}
    locked_atomic_write_json(pending_path, output)
    return {
        "observations": len(observations),
        "new_candidates": len(candidates),
        "pending_total": len(merged),
        "pending_path": str(pending_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate pending rule candidates from structured run evidence")
    parser.add_argument("--runs-dir", required=True)
    parser.add_argument("--pending", required=True, help="Path to pending_rules.json")
    parser.add_argument("--active-rules", default=None, help="Optional active_rules.json to avoid duplicate candidates")
    parser.add_argument("--min-occurrences", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = analyze_to_pending(
        runs_dir=args.runs_dir,
        pending_path=args.pending,
        active_rules_path=args.active_rules,
        min_occurrences=args.min_occurrences,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"observations: {summary['observations']}")
        print(f"new_candidates: {summary['new_candidates']}")
        print(f"pending_total: {summary['pending_total']}")
        print(f"pending_path: {summary['pending_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
