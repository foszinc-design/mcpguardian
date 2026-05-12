# MCPGuardian Phase 2

Phase 2 adds XLSX validation artifacts on top of the Phase 1 hard gate.

## What changed from Phase 1

Phase 1 checked whether required artifact files existed. Phase 2 tightens that behavior for known XLSX artifacts:

- `sheet_inventory.json` must be a valid `sheet_inventory` artifact.
- `row_count_summary.json` must be a valid `row_count_summary` artifact.
- `coverage_report.json` must be a valid `coverage_report` artifact with `global_claim_safe: true` before workbook-level/global claims are allowed.

An empty `{}` file no longer satisfies the gate.

## New module

```text
guardian/validators/xlsx_validator.py
```

It generates:

```text
sheet_inventory.json
row_count_summary.json
coverage_report.json
validator_result.json
```

## Install dependency

```bash
pip install -r requirements.txt
```

## Generate artifacts

Metadata-only generation:

```bash
python -m guardian.validators.xlsx_validator \
  --input F:\\Data\\report.xlsx \
  --output-dir F:\\Projects\\MCPGuardian\\runs\\20260512_153012_ab12cd
```

This creates inventory and row-count artifacts, but `coverage_report.json` will set `global_claim_safe: false` because metadata inspection alone does not prove analytical coverage.

After a full-workbook analysis, explicitly declare full coverage:

```bash
python -m guardian.validators.xlsx_validator \
  --input F:\\Data\\report.xlsx \
  --output-dir F:\\Projects\\MCPGuardian\\runs\\20260512_153012_ab12cd \
  --assume-full-analysis
```

Alternatively declare covered sheets:

```bash
python -m guardian.validators.xlsx_validator \
  --input F:\\Data\\report.xlsx \
  --output-dir F:\\Projects\\MCPGuardian\\runs\\20260512_153012_ab12cd \
  --analyzed-sheet Summary \
  --analyzed-sheet RawData
```

Use `--analyzed-sheet "*"` to declare all sheets covered.

## Run gate after artifact generation

```bash
python -m guardian.preflight_gate \
  --rules config/active_rules.json \
  --task-type xlsx_analysis \
  --requested-action "전체 매출 분석" \
  --input F:\\Data\\report.xlsx \
  --run-dir F:\\Projects\\MCPGuardian\\runs\\20260512_153012_ab12cd \
  --json
```

## Test

```bash
python -m unittest discover -s tests -v
```

Expected result:

```text
Ran 10 tests
OK
```

## Design note

`--assume-full-analysis` is intentionally explicit. The validator can prove workbook structure was inspected, but it cannot prove business calculations were correct. That proof belongs in Phase 4 claim-manifest validation.
