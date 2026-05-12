import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from guardian.atomic_io import locked_atomic_write_json
from guardian.mcp_tools import (
    GuardianPaths,
    mcpguardian_analyze_runs,
    mcpguardian_approve_rule,
    mcpguardian_list_pending_rules,
    mcpguardian_preflight,
    mcpguardian_validate_claim_manifest,
    mcpguardian_validate_xlsx,
)

ROOT = Path(__file__).resolve().parents[1]


class McpToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        locked_atomic_write_json(
            self.root / "config" / "mcp_guardian_config.json",
            {
                "schema_version": "1.0",
                "enable_rule_mutation": False,
                "paths": {
                    "runs_dir": "runs",
                    "active_rules": "config/active_rules.json",
                    "pending_rules": "config/pending_rules.json",
                    "rule_history": "config/rule_history.jsonl",
                },
                "allowed_roots": ["."],
            },
        )
        self.config = str(self.root / "config" / "mcp_guardian_config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _make_workbook(self, name="book.xlsx") -> Path:
        path = self.root / name
        wb = Workbook()
        ws = wb.active
        ws.title = "Summary"
        ws.append(["month", "sales"])
        ws.append(["2026-04", 100])
        ws.append(["2026-05", 112])
        hidden = wb.create_sheet("HiddenData")
        hidden.sheet_state = "hidden"
        hidden.append(["secret", 1])
        wb.save(path)
        return path

    def test_guardian_paths_loads_config(self):
        paths = GuardianPaths.load(root=self.root, config_path=self.config)
        self.assertEqual(paths.root, self.root.resolve())
        self.assertFalse(paths.rule_mutation_enabled)
        self.assertTrue(paths.active_rules.exists())

    def test_preflight_returns_stable_envelope_and_trace(self):
        input_path = self.root / "dummy.xlsx"
        result = mcpguardian_preflight(
            task_type="xlsx_analysis",
            requested_action="전체 매출 분석",
            input_paths=[str(input_path)],
            root=str(self.root),
            config_path=self.config,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "require_artifact")
        self.assertIn("sheet_inventory.json", result["required_artifacts"])
        self.assertTrue(result["run_id"])
        run_dir = self.root / "runs" / result["run_id"]
        self.assertTrue((run_dir / "trace.jsonl").exists())
        self.assertTrue((run_dir / "input_manifest.json").exists())

    def test_preflight_denies_paths_outside_allowed_roots(self):
        outside = Path(tempfile.gettempdir()) / "outside_mcpguardian.xlsx"
        result = mcpguardian_preflight(
            task_type="xlsx_analysis",
            requested_action="전체 매출 분석",
            input_paths=[str(outside)],
            root=str(self.root),
            config_path=self.config,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "PATH_POLICY_DENIED")

    def test_validate_xlsx_generates_four_artifacts(self):
        workbook = self._make_workbook()
        result = mcpguardian_validate_xlsx(
            input_path=str(workbook),
            assume_full_analysis=True,
            root=str(self.root),
            config_path=self.config,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["artifacts"]), 4)
        run_dir = self.root / "runs" / result["run_id"]
        for name in ["sheet_inventory.json", "row_count_summary.json", "coverage_report.json", "validator_result.json"]:
            self.assertTrue((run_dir / name).exists(), name)
        coverage = json.loads((run_dir / "coverage_report.json").read_text(encoding="utf-8"))
        self.assertTrue(coverage["global_claim_safe"])

    def test_claim_manifest_validation_writes_result_artifact(self):
        run_dir = self.root / "runs" / "manual_run"
        run_dir.mkdir()
        locked_atomic_write_json(run_dir / "computed_metrics.json", {"artifact_type": "computed_metrics"})
        locked_atomic_write_json(
            run_dir / "claim_manifest.json",
            {
                "artifact_type": "claim_manifest",
                "schema_version": "1.0",
                "claims": [
                    {
                        "claim_id": "claim_001",
                        "text": "총 매출은 전월 대비 12.4% 증가했다.",
                        "type": "quantitative",
                        "source_artifacts": ["computed_metrics.json"],
                        "calculation": "((current_month - previous_month) / previous_month) * 100",
                        "verified": True,
                    }
                ],
            },
        )
        result = mcpguardian_validate_claim_manifest(
            manifest_path=str(run_dir / "claim_manifest.json"),
            run_dir=str(run_dir),
            root=str(self.root),
            config_path=self.config,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue((run_dir / "claim_manifest_validator_result.json").exists())

    def test_analyze_and_list_pending_rules_are_read_or_pending_only(self):
        result = mcpguardian_analyze_runs(root=str(self.root), config_path=self.config, min_occurrences=2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["pending_path"], str(self.root / "config" / "pending_rules.json"))
        listed = mcpguardian_list_pending_rules(root=str(self.root), config_path=self.config)
        self.assertTrue(listed["ok"], listed)
        self.assertIn("rules", listed["data"])

    def test_rule_mutation_disabled_by_default(self):
        result = mcpguardian_approve_rule(rule_id="candidate.anything", root=str(self.root), config_path=self.config)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "PERMISSION_DENIED")

    def test_rule_mutation_requires_env_and_config(self):
        locked_atomic_write_json(
            self.root / "config" / "mcp_guardian_config.json",
            {
                "schema_version": "1.0",
                "enable_rule_mutation": True,
                "paths": {
                    "runs_dir": "runs",
                    "active_rules": "config/active_rules.json",
                    "pending_rules": "config/pending_rules.json",
                    "rule_history": "config/rule_history.jsonl",
                },
                "allowed_roots": ["."],
            },
        )
        locked_atomic_write_json(
            self.root / "config" / "pending_rules.json",
            {
                "rules": [
                    {
                        "id": "candidate.test.require_dummy.v1",
                        "status": "proposed",
                        "review_required": True,
                        "scope": "test",
                        "task_types": ["test_task"],
                        "proposed_enforcement": "require_artifact",
                        "proposed_severity": "medium",
                        "proposed_condition": {"missing_any_artifacts": ["dummy.json"]},
                        "proposed_required_artifacts": ["dummy.json"],
                        "proposed_message": "dummy required",
                        "evidence_refs": [],
                    }
                ]
            },
        )
        old = os.environ.get("MCPGUARDIAN_ENABLE_RULE_MUTATION")
        os.environ["MCPGUARDIAN_ENABLE_RULE_MUTATION"] = "1"
        try:
            result = mcpguardian_approve_rule(
                rule_id="candidate.test.require_dummy.v1",
                note="approved by unit test",
                root=str(self.root),
                config_path=self.config,
            )
        finally:
            if old is None:
                os.environ.pop("MCPGUARDIAN_ENABLE_RULE_MUTATION", None)
            else:
                os.environ["MCPGUARDIAN_ENABLE_RULE_MUTATION"] = old
        self.assertTrue(result["ok"], result)
        active = json.loads((self.root / "config" / "active_rules.json").read_text(encoding="utf-8"))
        self.assertTrue(any(rule["id"] == "test.require_dummy.v1" for rule in active["rules"]))


if __name__ == "__main__":
    unittest.main()
