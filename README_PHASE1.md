# MCPGuardian Phase 1 Core

Phase 1 implements the minimum hard-control surface:

1. `preflight_gate.py` decides `allow`, `warn`, `require_artifact`, `block`, or `postcheck_required`.
2. `structured_trace.py` creates isolated run directories and writes JSONL trace events.
3. `atomic_io.py` provides locked atomic JSON writes and append-safe JSONL writes.
4. `active_rules.json` seeds a small set of narrow enforcement rules.

## Run tests

```bash
cd mcpguardian_phase1
python -m unittest discover -s tests
```

## Example gate invocation

```bash
python -m guardian.preflight_gate \
  --rules config/active_rules.json \
  --task-type xlsx_analysis \
  --requested-action "전체 매출 분석" \
  --input F:\\Data\\report.xlsx \
  --run-dir runs/demo \
  --json
```

Expected result when required artifacts are missing:

```json
{
  "decision": "require_artifact",
  "risk_level": "critical",
  "matched_rules": [
    "xlsx.require_sheet_inventory.v1",
    "xlsx.block_sample_only_global_claim.v1"
  ],
  "required_artifacts": [
    "coverage_report.json",
    "row_count_summary.json",
    "sheet_inventory.json"
  ]
}
```

## Operating rule

Do not load `pending_rules.json` into the gate. Only `active_rules.json` is enforcement input.


## Phase 2 note

See `README_PHASE2.md` for XLSX validator artifacts and stricter artifact-shape gate checks.
