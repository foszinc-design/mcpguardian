"""Hard preflight gate for MCPGuardian Phase 1.

This module deliberately does not merely print advice. It returns an executable
allow/warn/require_artifact/block/postcheck_required decision.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .atomic_io import load_json
from .schemas import Decision, GateDecision, GateRequest, InputFile, Rule

_DECISION_ORDER = {
    Decision.ALLOW.value: 0,
    Decision.WARN.value: 1,
    Decision.POSTCHECK_REQUIRED.value: 2,
    Decision.REQUIRE_ARTIFACT.value: 3,
    Decision.BLOCK.value: 4,
}

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

SUPPORTED_CONDITION_KEYS = {
    "input_file_ext",
    "requested_actions_any",
    "contains_quantitative_claim",
    "global_claim_keywords",
    "missing_any_artifacts",
    "mcp_backend",
    "mcp_backend_any",
    "mcp_tool_name",
    "mcp_tool_name_any",
    "command_contains_any",
    "command_regex_any",
}



def load_active_rules(path: str | Path) -> list[Rule]:
    raw = load_json(path, default={"rules": []})
    rules_obj = raw.get("rules", raw if isinstance(raw, list) else [])
    return [Rule.from_dict(item) for item in rules_obj]


def _valid_json_artifact(path: Path, artifact_type: str, required_keys: list[str]) -> bool:
    try:
        obj = load_json(path)
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    if obj.get("artifact_type") != artifact_type:
        return False
    return all(key in obj for key in required_keys)


def artifact_satisfies(run_dir: str | Path, artifact_name: str, existing_artifacts: list[str]) -> bool:
    """Return True only when an artifact exists and passes minimum shape checks.

    Phase 1 only checked file presence. Phase 2 tightens this for known XLSX
    artifacts so an empty `{}` cannot satisfy a hard gate. `existing_artifacts`
    remains a trusted escape hatch for callers that have already validated an
    in-memory artifact before calling the gate.
    """
    if artifact_name in existing_artifacts:
        return True
    path = Path(run_dir) / artifact_name
    if not path.exists() or not path.is_file():
        return False

    if artifact_name == "sheet_inventory.json":
        return _valid_json_artifact(path, "sheet_inventory", ["sheet_count", "sheets"])
    if artifact_name == "row_count_summary.json":
        return _valid_json_artifact(path, "row_count_summary", ["workbook_total_non_empty_rows", "sheets"])
    if artifact_name == "coverage_report.json":
        try:
            obj = load_json(path)
        except Exception:
            return False
        return (
            isinstance(obj, dict)
            and obj.get("artifact_type") == "coverage_report"
            and obj.get("global_claim_safe") is True
        )
    if artifact_name == "claim_manifest.json":
        try:
            from .validators.claim_manifest_validator import validate_claim_manifest

            result = validate_claim_manifest(path, run_dir=Path(run_dir))
        except Exception:
            return False
        return (
            result.get("ok") is True
            and int(result.get("quantitative_claim_count") or 0) > 0
            and result.get("quantitative_claim_count") == result.get("verified_quantitative_claim_count")
        )

    return True


def _any_text_match(needles: list[str], haystack: str) -> bool:
    lower = haystack.lower()
    return any(needle.lower() in lower for needle in needles)


def _condition_matches(rule: Rule, request: GateRequest) -> bool:
    cond = rule.condition or {}

    unsupported = set(cond) - SUPPORTED_CONDITION_KEYS
    if unsupported:
        # Unknown condition keys must not be silently ignored. Otherwise a
        # newly approved rule can become much broader than intended.
        return False

    if rule.task_types and request.task_type not in rule.task_types and "*" not in rule.task_types:
        return False

    if "input_file_ext" in cond:
        allowed_ext = {str(ext).lower() for ext in cond["input_file_ext"]}
        if not any(item.extension.lower() in allowed_ext for item in request.input_files):
            return False

    if "requested_actions_any" in cond:
        if not _any_text_match(list(cond["requested_actions_any"]), request.requested_action):
            return False

    if cond.get("contains_quantitative_claim") is True:
        # Phase 1 approximation. Phase 4 replaces this with a real claim manifest validator.
        quantitative_tokens = ["%", "증가", "감소", "합계", "총", "평균", "최대", "최소", "전월", "전년", "ratio", "sum", "average"]
        if not _any_text_match(quantitative_tokens, request.requested_action):
            return False

    if "global_claim_keywords" in cond:
        if not _any_text_match(list(cond["global_claim_keywords"]), request.requested_action):
            return False

    if cond.get("missing_any_artifacts"):
        missing = [name for name in cond["missing_any_artifacts"] if not artifact_satisfies(request.run_dir, name, request.existing_artifacts)]
        if not missing:
            return False

    context = request.context or {}
    if "mcp_backend" in cond and context.get("mcp_backend") != cond["mcp_backend"]:
        return False

    if "mcp_backend_any" in cond:
        allowed_backends = {str(item) for item in cond["mcp_backend_any"]}
        if str(context.get("mcp_backend")) not in allowed_backends:
            return False

    if "mcp_tool_name" in cond and context.get("mcp_tool_name") != cond["mcp_tool_name"]:
        return False

    if "mcp_tool_name_any" in cond:
        allowed_tools = {str(item) for item in cond["mcp_tool_name_any"]}
        if str(context.get("mcp_tool_name")) not in allowed_tools:
            return False

    command_text = str(context.get("command") or request.requested_action or "")
    if "command_contains_any" in cond:
        needles = [str(item) for item in cond["command_contains_any"]]
        if not _any_text_match(needles, command_text):
            return False

    if "command_regex_any" in cond:
        import re

        patterns = [str(item) for item in cond["command_regex_any"]]
        if not any(re.search(pattern, command_text, flags=re.I) for pattern in patterns):
            return False

    return True


def evaluate_gate(request: GateRequest, rules: list[Rule]) -> GateDecision:
    matched: list[Rule] = []
    required: set[str] = set()
    messages: list[str] = []
    final_decision = Decision.ALLOW.value
    max_risk = "low"

    for rule in rules:
        if not _condition_matches(rule, request):
            continue
        matched.append(rule)
        messages.append(f"[{rule.id}] {rule.message}")
        max_risk = max(max_risk, rule.severity, key=lambda value: _RISK_ORDER.get(value, 0))

        missing_required = [
            name for name in rule.required_artifacts if not artifact_satisfies(request.run_dir, name, request.existing_artifacts)
        ]
        required.update(missing_required)

        proposed = rule.enforcement
        if proposed in {Decision.REQUIRE_ARTIFACT.value, Decision.POSTCHECK_REQUIRED.value} and not missing_required:
            proposed = Decision.ALLOW.value
        if _DECISION_ORDER[proposed] > _DECISION_ORDER[final_decision]:
            final_decision = proposed

    return GateDecision(
        decision=final_decision,
        risk_level=max_risk,
        matched_rules=[rule.id for rule in matched],
        required_artifacts=sorted(required),
        messages=messages,
    )


def build_request(args: argparse.Namespace) -> GateRequest:
    input_files = [InputFile.from_path(item) for item in args.input]
    return GateRequest(
        task_type=args.task_type,
        requested_action=args.requested_action or " ".join(args.input),
        input_files=input_files,
        run_dir=args.run_dir,
        existing_artifacts=args.existing_artifact or [],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCPGuardian hard preflight gate")
    parser.add_argument("--rules", required=True, help="Path to active_rules.json")
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--requested-action", default="")
    parser.add_argument("--input", action="append", default=[], help="Input file path. Can be repeated.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--existing-artifact", action="append", default=[])
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    args = parser.parse_args(argv)

    request = build_request(args)
    rules = load_active_rules(args.rules)
    decision = evaluate_gate(request, rules)

    if args.json:
        print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"decision: {decision.decision}")
        print(f"risk_level: {decision.risk_level}")
        print(f"matched_rules: {', '.join(decision.matched_rules) if decision.matched_rules else '-'}")
        print(f"required_artifacts: {', '.join(decision.required_artifacts) if decision.required_artifacts else '-'}")
        for message in decision.messages:
            print(f"- {message}")

    return 2 if decision.decision in {Decision.BLOCK.value, Decision.REQUIRE_ARTIFACT.value} else 0


if __name__ == "__main__":
    raise SystemExit(main())
