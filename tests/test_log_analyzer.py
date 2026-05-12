from pathlib import Path
import tempfile
import unittest

from guardian.atomic_io import append_jsonl, load_json, locked_atomic_write_json
from guardian.log_analyzer import analyze_to_pending, collect_run_observations, generate_rule_candidates


class LogAnalyzerTests(unittest.TestCase):
    def _make_run(self, root: Path, run_id: str, artifact: str = "quality_gate.json") -> Path:
        run_dir = root / run_id
        run_dir.mkdir(parents=True)
        trace = run_dir / "trace.jsonl"
        append_jsonl(trace, {
            "event_type": "run_started",
            "run_id": run_id,
            "timestamp": "2026-05-12T00:00:00+09:00",
            "task_type": "spreadsheet_review",
            "requested_action": "전체 품질 검토",
            "input_files": ["report.xlsx"],
        })
        append_jsonl(trace, {
            "event_type": "preflight_evaluated",
            "run_id": run_id,
            "timestamp": "2026-05-12T00:00:01+09:00",
            "decision": "require_artifact",
            "required_artifacts": [artifact],
        })
        return run_dir

    def test_collects_missing_artifact_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_run(root, "run_a")
            observations = collect_run_observations(root)
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["kind"], "missing_required_artifact")
            self.assertEqual(observations[0]["artifact"], "quality_gate.json")
            self.assertEqual(observations[0]["extensions"], [".xlsx"])

    def test_generates_review_required_candidate_without_touching_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            self._make_run(runs, "run_a")
            self._make_run(runs, "run_b")
            pending = root / "pending_rules.json"
            active = root / "active_rules.json"
            locked_atomic_write_json(pending, {"rules": []})
            locked_atomic_write_json(active, {"rules": []})

            summary = analyze_to_pending(
                runs_dir=runs,
                pending_path=pending,
                active_rules_path=active,
                min_occurrences=2,
            )
            self.assertEqual(summary["new_candidates"], 1)
            self.assertEqual(load_json(active), {"rules": []})
            rules = load_json(pending)["rules"]
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["status"], "proposed")
            self.assertIs(rules[0]["review_required"], True)
            self.assertEqual(rules[0]["proposed_required_artifacts"], ["quality_gate.json"])

    def test_skips_existing_active_equivalent(self):
        observations = [
            {
                "kind": "missing_required_artifact",
                "task_type": "spreadsheet_review",
                "artifact": "sheet_inventory.json",
                "decision": "require_artifact",
                "extensions": [".xlsx"],
                "scope": "xlsx",
                "evidence_ref": "runs/a/trace.jsonl",
            },
            {
                "kind": "missing_required_artifact",
                "task_type": "spreadsheet_review",
                "artifact": "sheet_inventory.json",
                "decision": "require_artifact",
                "extensions": [".xlsx"],
                "scope": "xlsx",
                "evidence_ref": "runs/b/trace.jsonl",
            },
        ]
        active = [{
            "task_types": ["spreadsheet_review"],
            "required_artifacts": ["sheet_inventory.json"],
            "enforcement": "require_artifact",
        }]
        candidates = generate_rule_candidates(observations, active_rules=active, min_occurrences=2)
        self.assertEqual(candidates, [])


class LogAnalyzerPhase4Tests(unittest.TestCase):
    def test_collects_named_validator_result_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            run = runs / "run_001"
            run.mkdir(parents=True)
            (run / "trace.jsonl").write_text(
                '{"event_type":"run_started","task_type":"report_generation","input_files":["report.md"]}\n',
                encoding="utf-8",
            )
            locked_atomic_write_json(
                run / "claim_manifest_validator_result.json",
                {
                    "artifact_type": "claim_manifest_validator_result",
                    "validator": "claim_manifest_validator",
                    "ok": False,
                    "errors": ["claims[0].verified must be true for quantitative claims."],
                    "warnings": [],
                },
            )
            observations = collect_run_observations(runs)
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["kind"], "validator_message")
            self.assertEqual(observations[0]["validator"], "claim_manifest_validator")
            self.assertIn("verified must be true", observations[0]["message"])


if __name__ == "__main__":
    unittest.main()
