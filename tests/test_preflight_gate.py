from pathlib import Path
import tempfile
import unittest

from guardian.preflight_gate import evaluate_gate, load_active_rules
from guardian.schemas import GateRequest, InputFile
from guardian.atomic_io import locked_atomic_write_json


ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "active_rules.json"


class PreflightGateTests(unittest.TestCase):
    def test_xlsx_requires_sheet_inventory(self):
        rules = load_active_rules(RULES)
        with tempfile.TemporaryDirectory() as tmp:
            req = GateRequest(
                task_type="xlsx_analysis",
                requested_action="전체 매출 분석",
                input_files=[InputFile(path="dummy.xlsx", extension=".xlsx", exists=False)],
                run_dir=tmp,
            )
            decision = evaluate_gate(req, rules)
            self.assertEqual(decision.decision, "require_artifact")
            self.assertIn("sheet_inventory.json", decision.required_artifacts)
            self.assertIn("xlsx.require_sheet_inventory.v1", decision.matched_rules)

    def test_xlsx_rejects_empty_artifact_shapes(self):
        rules = load_active_rules(RULES)
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["sheet_inventory.json", "row_count_summary.json", "coverage_report.json"]:
                Path(tmp, name).write_text("{}", encoding="utf-8")
            req = GateRequest(
                task_type="xlsx_analysis",
                requested_action="전체 매출 분석",
                input_files=[InputFile(path="dummy.xlsx", extension=".xlsx", exists=False)],
                run_dir=tmp,
            )
            decision = evaluate_gate(req, rules)
            self.assertEqual(decision.decision, "require_artifact")
            self.assertIn("sheet_inventory.json", decision.required_artifacts)
            self.assertIn("coverage_report.json", decision.required_artifacts)

    def test_xlsx_allows_when_valid_required_artifacts_exist(self):
        rules = load_active_rules(RULES)
        with tempfile.TemporaryDirectory() as tmp:
            locked_atomic_write_json(Path(tmp) / "sheet_inventory.json", {
                "artifact_type": "sheet_inventory",
                "sheet_count": 1,
                "sheets": [{"name": "Summary"}],
            })
            locked_atomic_write_json(Path(tmp) / "row_count_summary.json", {
                "artifact_type": "row_count_summary",
                "workbook_total_non_empty_rows": 10,
                "sheets": [{"name": "Summary", "non_empty_rows": 10}],
            })
            locked_atomic_write_json(Path(tmp) / "coverage_report.json", {
                "artifact_type": "coverage_report",
                "global_claim_safe": True,
            })
            req = GateRequest(
                task_type="xlsx_analysis",
                requested_action="전체 매출 분석",
                input_files=[InputFile(path="dummy.xlsx", extension=".xlsx", exists=False)],
                run_dir=tmp,
            )
            decision = evaluate_gate(req, rules)
            self.assertEqual(decision.decision, "allow")
            self.assertEqual(decision.required_artifacts, [])

    def test_code_edit_requires_change_manifest(self):
        rules = load_active_rules(RULES)
        with tempfile.TemporaryDirectory() as tmp:
            req = GateRequest(
                task_type="code_edit",
                requested_action="이 버그를 고쳐",
                input_files=[InputFile(path="app.py", extension=".py", exists=False)],
                run_dir=tmp,
            )
            decision = evaluate_gate(req, rules)
            self.assertEqual(decision.decision, "require_artifact")
            self.assertIn("change_manifest.json", decision.required_artifacts)

    def test_quant_report_requires_postcheck(self):
        rules = load_active_rules(RULES)
        with tempfile.TemporaryDirectory() as tmp:
            req = GateRequest(
                task_type="report_generation",
                requested_action="전월 대비 12% 증가 보고서를 작성",
                input_files=[],
                run_dir=tmp,
            )
            decision = evaluate_gate(req, rules)
            self.assertEqual(decision.decision, "postcheck_required")
            self.assertIn("claim_manifest.json", decision.required_artifacts)


if __name__ == "__main__":
    unittest.main()
