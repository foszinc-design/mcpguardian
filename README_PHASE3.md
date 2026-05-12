# MCPGuardian Phase 3

Phase 3 adds the rule-learning control plane without allowing automatic live enforcement.

## What changed from Phase 2

Phase 2 made XLSX artifacts real and validated their shape. Phase 3 adds:

- `guardian/log_analyzer.py`
- `guardian/rule_reviewer.py`
- `config/rule_history.jsonl`
- pending-rule merge semantics
- explicit approve/reject workflow
- active-rule duplicate avoidance
- unsupported condition-key protection in `preflight_gate.py`

The core invariant is unchanged:

```text
observations -> pending_rules.json -> human approval -> active_rules.json
```

`log_analyzer.py` never writes `active_rules.json`.

## Analyzer

Generate pending rule candidates from structured runs:

```bash
python -m guardian.log_analyzer \
  --runs-dir runs \
  --pending config/pending_rules.json \
  --active-rules config/active_rules.json \
  --min-occurrences 2
```

Machine-readable mode:

```bash
python -m guardian.log_analyzer \
  --runs-dir runs \
  --pending config/pending_rules.json \
  --active-rules config/active_rules.json \
  --min-occurrences 2 \
  --json
```

The analyzer currently creates candidates from two conservative evidence types:

1. repeated missing required artifacts from `preflight_evaluated` trace events
2. repeated validator warnings/errors from `validator_result.json`

It skips candidates equivalent to existing active rules when `--active-rules` is supplied.

## Pending rule schema

Example:

```json
{
  "id": "candidate.xlsx.require_quality_gate.v1",
  "target_rule_id": "xlsx.require_quality_gate.v1",
  "status": "proposed",
  "review_required": true,
  "scope": "xlsx",
  "task_types": ["spreadsheet_review"],
  "proposed_severity": "high",
  "proposed_enforcement": "require_artifact",
  "proposed_condition": {
    "input_file_ext": [".xlsx"]
  },
  "proposed_required_artifacts": ["quality_gate.json"],
  "proposed_message": "spreadsheet_review 작업은 quality_gate.json 없이는 통과시키지 않는다.",
  "evidence_refs": ["runs/run_a/trace.jsonl"],
  "observed_failure": "2 run(s) required missing artifact quality_gate.json during require_artifact.",
  "false_positive_risk": "medium"
}
```

## Reviewer

List pending rules:

```bash
python -m guardian.rule_reviewer \
  --pending config/pending_rules.json \
  --active-rules config/active_rules.json \
  --history config/rule_history.jsonl \
  list --status proposed
```

Approve one candidate:

```bash
python -m guardian.rule_reviewer \
  --pending config/pending_rules.json \
  --active-rules config/active_rules.json \
  --history config/rule_history.jsonl \
  approve --rule-id candidate.xlsx.require_quality_gate.v1 --note "reviewed evidence"
```

Reject one candidate:

```bash
python -m guardian.rule_reviewer \
  --pending config/pending_rules.json \
  --active-rules config/active_rules.json \
  --history config/rule_history.jsonl \
  reject --rule-id candidate.xlsx.require_quality_gate.v1 --reason "too broad"
```

## Safety guarantees

Phase 3 enforces these boundaries:

- analyzer cannot update active rules
- pending rules must have `review_required: true`
- only `status: proposed` rules can be approved
- approved rules are strict-validated with `Rule.from_dict`
- unsupported condition keys are rejected during approval
- gate no longer silently ignores unknown condition keys
- approvals and rejections are appended to `rule_history.jsonl`
- active/pending updates use a shared `rule_state.lock`
- writes remain atomic

## Test status

```text
Ran 16 tests in 0.104s

OK
```
