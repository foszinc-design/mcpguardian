# MCPGuardian Phase 4

Phase 4 adds explicit quantitative-claim evidence tracking.

The intent is deliberately narrow: MCPGuardian does not try to infer every fact
from prose. Instead, report-producing workflows must emit a `claim_manifest.json`
that registers important quantitative claims and links each claim to evidence
artifacts.

## Added files

```text
guardian/validators/claim_manifest_validator.py
tests/test_claim_manifest_validator.py
```

`log_analyzer.py` was also extended to read named validator result files such as
`claim_manifest_validator_result.json` in addition to the generic
`validator_result.json`.

## Claim manifest schema

Minimum valid shape:

```json
{
  "artifact_type": "claim_manifest",
  "schema_version": "1.0",
  "generated_at": "2026-05-12T15:30:00+09:00",
  "source_document": "report.md",
  "claims": [
    {
      "claim_id": "claim_001",
      "text": "총 매출은 전월 대비 12.4% 증가했다.",
      "type": "quantitative",
      "source_artifacts": ["computed_metrics.json"],
      "source_cells": ["Sales!G42", "Sales!G43"],
      "calculation": "((current_month - previous_month) / previous_month) * 100",
      "verified": true
    }
  ]
}
```

For quantitative claims, the validator requires:

- unique non-empty `claim_id`
- non-empty `text`
- `type` such as `quantitative`, `metric`, `calculation`, or `comparison`
- non-empty `source_artifacts`
- every referenced source artifact must exist under the run directory unless an absolute path is used
- non-empty `calculation`
- `verified: true`

## CLI

Create an empty skeleton:

```bash
python -m guardian.validators.claim_manifest_validator init \
  --output runs/demo/claim_manifest.json \
  --source-document report.md
```

Validate a manifest:

```bash
python -m guardian.validators.claim_manifest_validator validate \
  --manifest runs/demo/claim_manifest.json \
  --run-dir runs/demo \
  --result-out runs/demo/claim_manifest_validator_result.json
```

Validate manifest coverage against a markdown report:

```bash
python -m guardian.validators.claim_manifest_validator validate \
  --manifest runs/demo/claim_manifest.json \
  --run-dir runs/demo \
  --output-document runs/demo/report.md \
  --strict-output-coverage \
  --result-out runs/demo/claim_manifest_validator_result.json
```

`--strict-output-coverage` promotes uncovered quantitative-looking report
sentences from warnings to errors. This scan is heuristic. It is a postcheck,
not a replacement for explicit claim registration.

## Gate behavior

`preflight_gate.py` no longer accepts a shallow `claim_manifest.json` with only
`artifact_type` and `claims`. To satisfy the quantitative-report gate, the
manifest must validate successfully and contain at least one verified
quantitative claim.

An empty manifest skeleton is useful for manual population, but it does not
satisfy the report quantitative-claim gate.

## Key design boundary

Phase 4 keeps the same principle as the earlier phases:

```text
explicit evidence > inferred confidence
```

The validator is intentionally strict about declared claims and conservative
about inferred output coverage. This avoids turning a heuristic prose scanner
into another false guarantee.
