from pathlib import Path
import tempfile
import unittest

from guardian.atomic_io import locked_atomic_write_json
from guardian.preflight_gate import evaluate_gate, load_active_rules
from guardian.schemas import GateRequest
from guardian.validators.claim_manifest_validator import (
    empty_claim_manifest,
    extract_quantitative_sentences,
    validate_claim_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "active_rules.json"


class ClaimManifestValidatorTests(unittest.TestCase):
    def test_empty_manifest_skeleton_is_well_formed_but_not_gate_satisfying(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "claim_manifest.json"
            locked_atomic_write_json(manifest_path, empty_claim_manifest(source_document="report.md"))
            result = validate_claim_manifest(manifest_path, run_dir=tmp)
            self.assertTrue(result["ok"])
            self.assertEqual(result["quantitative_claim_count"], 0)

            rules = load_active_rules(RULES)
            decision = evaluate_gate(
                GateRequest(
                    task_type="report_generation",
                    requested_action="전월 대비 12% 증가 보고서를 작성",
                    input_files=[],
                    run_dir=tmp,
                ),
                rules,
            )
            self.assertEqual(decision.decision, "postcheck_required")
            self.assertIn("claim_manifest.json", decision.required_artifacts)

    def test_valid_quantitative_claim_manifest_satisfies_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked_atomic_write_json(Path(tmp) / "computed_metrics.json", {"artifact_type": "computed_metrics"})
            locked_atomic_write_json(
                Path(tmp) / "claim_manifest.json",
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
            result = validate_claim_manifest(Path(tmp) / "claim_manifest.json", run_dir=tmp)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["verified_quantitative_claim_count"], 1)

            decision = evaluate_gate(
                GateRequest(
                    task_type="report_generation",
                    requested_action="전월 대비 12% 증가 보고서를 작성",
                    input_files=[],
                    run_dir=tmp,
                ),
                load_active_rules(RULES),
            )
            self.assertEqual(decision.decision, "allow")
            self.assertEqual(decision.required_artifacts, [])

    def test_quantitative_claim_requires_existing_source_artifact_calculation_and_verified_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked_atomic_write_json(
                Path(tmp) / "claim_manifest.json",
                {
                    "artifact_type": "claim_manifest",
                    "claims": [
                        {
                            "claim_id": "claim_001",
                            "text": "평균 처리 시간은 18% 감소했다.",
                            "type": "quantitative",
                            "source_artifacts": ["missing_metrics.json"],
                            "verified": False,
                        }
                    ],
                },
            )
            result = validate_claim_manifest(Path(tmp) / "claim_manifest.json", run_dir=tmp)
            self.assertFalse(result["ok"])
            joined = "\n".join(result["errors"])
            self.assertIn("missing files", joined)
            self.assertIn("verified must be true", joined)
            self.assertIn("calculation is required", joined)

    def test_output_document_strict_coverage_fails_on_uncovered_quant_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked_atomic_write_json(Path(tmp) / "computed_metrics.json", {"artifact_type": "computed_metrics"})
            locked_atomic_write_json(
                Path(tmp) / "claim_manifest.json",
                {
                    "artifact_type": "claim_manifest",
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
            report = Path(tmp) / "report.md"
            report.write_text(
                "총 매출은 전월 대비 12.4% 증가했다.\n평균 비용은 7% 감소했다.\n",
                encoding="utf-8",
            )
            result = validate_claim_manifest(
                Path(tmp) / "claim_manifest.json",
                run_dir=tmp,
                output_document=report,
                strict_output_coverage=True,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["output_quantitative_sentence_count"], 2)
            self.assertEqual(len(result["uncovered_output_quantitative_sentences"]), 1)

    def test_quantitative_sentence_extractor_ignores_plain_section_numbers(self):
        text = "# 1. 개요\n2. 방법론\n총 매출은 12% 증가했다.\n"
        sentences = extract_quantitative_sentences(text)
        self.assertEqual(sentences, ["총 매출은 12% 증가했다."])


if __name__ == "__main__":
    unittest.main()
