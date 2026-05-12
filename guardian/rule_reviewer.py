"""Human approval workflow for MCPGuardian Phase 3 pending rules.

This module is the only sanctioned path from pending_rules.json to
active_rules.json. The log analyzer never touches active rules.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .atomic_io import append_jsonl, atomic_write_json, file_lock, load_json
from .preflight_gate import SUPPORTED_CONDITION_KEYS
from .schemas import Rule, utc_now_iso


def _load_rule_list(path: str | Path) -> list[dict[str, Any]]:
    raw = load_json(path, default={"rules": []})
    if isinstance(raw, dict):
        return list(raw.get("rules", []))
    if isinstance(raw, list):
        return raw
    return []


def _write_rule_list(path: str | Path, rules: list[dict[str, Any]]) -> None:
    atomic_write_json(path, {"rules": rules})


def _find_rule(rules: list[dict[str, Any]], rule_id: str) -> tuple[int, dict[str, Any]]:
    for idx, rule in enumerate(rules):
        if rule.get("id") == rule_id or rule.get("target_rule_id") == rule_id:
            return idx, rule
    raise ValueError(f"Rule not found: {rule_id}")


def _validate_supported_condition(condition: dict[str, Any]) -> None:
    unsupported = sorted(set(condition) - SUPPORTED_CONDITION_KEYS)
    if unsupported:
        raise ValueError(f"Unsupported condition keys for active gate: {', '.join(unsupported)}")


def pending_to_active_rule(pending: dict[str, Any]) -> dict[str, Any]:
    if pending.get("status") != "proposed":
        raise ValueError(f"Only proposed rules can be approved: {pending.get('id')}")
    if pending.get("review_required") is not True:
        raise ValueError(f"Pending rule must require review before approval: {pending.get('id')}")

    condition = dict(pending.get("proposed_condition") or {})
    _validate_supported_condition(condition)
    active = {
        "id": pending.get("target_rule_id") or str(pending["id"]).replace("candidate.", "", 1),
        "status": "active",
        "scope": pending["scope"],
        "task_types": list(pending.get("task_types") or []),
        "severity": pending.get("proposed_severity") or "medium",
        "enforcement": pending["proposed_enforcement"],
        "condition": condition,
        "required_artifacts": list(pending.get("proposed_required_artifacts") or []),
        "message": pending.get("proposed_message") or pending.get("observed_failure") or "Approved MCPGuardian rule.",
        "created_at": utc_now_iso(),
        "created_by": "human_review",
        "evidence_refs": list(pending.get("evidence_refs") or []),
        "rollback_of": pending.get("rollback_of"),
    }
    # Reuse strict active-rule validation before writing.
    Rule.from_dict(active)
    return active


def approve_rule(
    *,
    rule_id: str,
    pending_path: str | Path,
    active_path: str | Path,
    history_path: str | Path,
    note: str | None = None,
) -> dict[str, Any]:
    lock_path = Path(active_path).parent / "rule_state.lock"
    with file_lock(lock_path):
        pending_rules = _load_rule_list(pending_path)
        active_rules = _load_rule_list(active_path)
        idx, pending = _find_rule(pending_rules, rule_id)
        active = pending_to_active_rule(pending)
        if any(rule.get("id") == active["id"] for rule in active_rules):
            raise ValueError(f"Active rule already exists: {active['id']}")
        active_rules.append(active)
        pending = dict(pending)
        pending.update(
            {
                "status": "staged",
                "approved_at": utc_now_iso(),
                "approved_active_rule_id": active["id"],
                "approval_note": note,
            }
        )
        pending_rules[idx] = pending
        _write_rule_list(active_path, sorted(active_rules, key=lambda item: str(item.get("id"))))
        _write_rule_list(pending_path, sorted(pending_rules, key=lambda item: str(item.get("id"))))

    history = {
        "timestamp": utc_now_iso(),
        "action": "approve",
        "pending_rule_id": pending["id"],
        "active_rule_id": active["id"],
        "note": note,
        "evidence_refs": active.get("evidence_refs", []),
    }
    append_jsonl(history_path, history)
    return {"active_rule": active, "pending_rule": pending, "history": history}


def reject_rule(
    *,
    rule_id: str,
    pending_path: str | Path,
    history_path: str | Path,
    reason: str,
) -> dict[str, Any]:
    lock_path = Path(pending_path).parent / "rule_state.lock"
    with file_lock(lock_path):
        pending_rules = _load_rule_list(pending_path)
        idx, pending = _find_rule(pending_rules, rule_id)
        pending = dict(pending)
        if pending.get("status") not in {"proposed", "staged"}:
            raise ValueError(f"Only proposed or staged pending rules can be rejected: {pending.get('id')}")
        pending.update({"status": "rejected", "rejected_at": utc_now_iso(), "rejection_reason": reason})
        pending_rules[idx] = pending
        _write_rule_list(pending_path, sorted(pending_rules, key=lambda item: str(item.get("id"))))

    history = {
        "timestamp": utc_now_iso(),
        "action": "reject",
        "pending_rule_id": pending["id"],
        "reason": reason,
        "evidence_refs": pending.get("evidence_refs", []),
    }
    append_jsonl(history_path, history)
    return {"pending_rule": pending, "history": history}


def list_rules(*, pending_path: str | Path, status: str | None = None) -> list[dict[str, Any]]:
    rules = _load_rule_list(pending_path)
    if status:
        rules = [rule for rule in rules if rule.get("status") == status]
    return sorted(rules, key=lambda item: str(item.get("id")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review pending MCPGuardian rule candidates")
    parser.add_argument("--pending", required=True, help="Path to pending_rules.json")
    parser.add_argument("--active-rules", required=True, help="Path to active_rules.json")
    parser.add_argument("--history", required=True, help="Path to rule_history.jsonl")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--status", default=None)

    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--rule-id", required=True)
    approve_parser.add_argument("--note", default=None)

    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("--rule-id", required=True)
    reject_parser.add_argument("--reason", required=True)

    args = parser.parse_args(argv)

    if args.command == "list":
        result: Any = list_rules(pending_path=args.pending, status=args.status)
    elif args.command == "approve":
        result = approve_rule(
            rule_id=args.rule_id,
            pending_path=args.pending,
            active_path=args.active_rules,
            history_path=args.history,
            note=args.note,
        )
    elif args.command == "reject":
        result = reject_rule(
            rule_id=args.rule_id,
            pending_path=args.pending,
            history_path=args.history,
            reason=args.reason,
        )
    else:
        raise AssertionError(args.command)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if isinstance(result, list):
            for rule in result:
                print(f"{rule.get('id')}\t{rule.get('status')}\t{rule.get('target_rule_id', '-')}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
