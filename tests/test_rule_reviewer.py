from pathlib import Path
import tempfile
import unittest

from guardian.atomic_io import load_json, locked_atomic_write_json
from guardian.rule_reviewer import approve_rule, reject_rule, pending_to_active_rule


def _candidate(rule_id: str = "candidate.xlsx.require_quality_gate.v1") -> dict:
    return {
        "id": rule_id,
        "target_rule_id": "xlsx.require_quality_gate.v1",
        "status": "proposed",
        "review_required": True,
        "scope": "xlsx",
        "task_types": ["spreadsheet_review"],
        "proposed_severity": "high",
        "proposed_enforcement": "require_artifact",
        "proposed_condition": {"input_file_ext": [".xlsx"]},
        "proposed_required_artifacts": ["quality_gate.json"],
        "proposed_message": "Spreadsheet review requires quality_gate.json.",
        "evidence_refs": ["runs/a/trace.jsonl"],
    }


class RuleReviewerTests(unittest.TestCase):
    def test_pending_to_active_rejects_unsupported_condition_keys(self):
        candidate = _candidate()
        candidate["proposed_condition"] = {"workbook_contains_hidden_sheets": True}
        with self.assertRaises(ValueError):
            pending_to_active_rule(candidate)

    def test_approve_moves_candidate_to_active_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / "pending_rules.json"
            active = root / "active_rules.json"
            history = root / "rule_history.jsonl"
            locked_atomic_write_json(pending, {"rules": [_candidate()]})
            locked_atomic_write_json(active, {"rules": []})

            result = approve_rule(
                rule_id="candidate.xlsx.require_quality_gate.v1",
                pending_path=pending,
                active_path=active,
                history_path=history,
                note="approved in test",
            )

            self.assertEqual(result["active_rule"]["id"], "xlsx.require_quality_gate.v1")
            self.assertEqual(load_json(active)["rules"][0]["status"], "active")
            self.assertEqual(load_json(pending)["rules"][0]["status"], "staged")
            self.assertIn('"action": "approve"', history.read_text(encoding="utf-8"))

    def test_reject_marks_pending_rule_and_writes_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / "pending_rules.json"
            history = root / "rule_history.jsonl"
            locked_atomic_write_json(pending, {"rules": [_candidate()]})

            result = reject_rule(
                rule_id="candidate.xlsx.require_quality_gate.v1",
                pending_path=pending,
                history_path=history,
                reason="too broad",
            )

            self.assertEqual(result["pending_rule"]["status"], "rejected")
            self.assertEqual(load_json(pending)["rules"][0]["rejection_reason"], "too broad")
            self.assertIn('"action": "reject"', history.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
