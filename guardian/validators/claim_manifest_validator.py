"""Claim manifest validator for MCPGuardian Phase 4.

This validator does not try to solve full natural-language fact extraction.
It validates the explicit claim manifest that a report generator must emit when
it makes quantitative claims. The hard guarantee is narrower and operationally
useful: every declared quantitative claim must have evidence artifacts,
calculation metadata, and an explicit verified=true marker.

Optional output-document scanning can flag quantitative-looking sentences that
are not covered by the manifest. That scan is heuristic by design and should be
used as a postcheck, not as a substitute for explicit claim registration.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ..atomic_io import load_json, locked_atomic_write_json, sha256_file
from ..schemas import utc_now_iso

CLAIM_MANIFEST_SCHEMA_VERSION = "1.0"
QUANTITATIVE_CLAIM_TYPES = {"quantitative", "metric", "calculation", "comparison"}

# Korean-first workflow, with enough English tokens for mixed technical reports.
QUANTITATIVE_PATTERNS = [
    re.compile(r"[-+]?\d+(?:\.\d+)?\s?%"),
    re.compile(r"[-+]?\d+(?:\.\d+)?\s?(?:원|만원|억원|조원|달러|usd|krw|개|건|명|회|배|rows?|cells?)", re.IGNORECASE),
    re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"),
    re.compile(r"\b\d+(?:\.\d+)?\b"),
]

QUANTITATIVE_KEYWORDS = {
    "증가", "감소", "상승", "하락", "전월", "전년", "대비", "비율", "합계", "총", "전체",
    "평균", "중앙값", "최대", "최소", "증감", "성장률", "점유율", "매출", "비용", "수익",
    "increase", "decrease", "growth", "ratio", "rate", "total", "sum", "average", "mean",
    "median", "maximum", "minimum", "revenue", "cost", "profit", "compared", "coverage",
}

REQUIRED_CLAIM_FIELDS = {"claim_id", "text", "type", "source_artifacts", "verified"}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def looks_quantitative(text: str) -> bool:
    """Heuristic detector for quantitative-looking natural language.

    A bare number is not enough. The sentence must contain either a numerical
    pattern plus a quantitative keyword, or a percentage/unit expression.
    """
    lowered = text.lower()
    has_pattern = any(pattern.search(text) for pattern in QUANTITATIVE_PATTERNS)
    if not has_pattern:
        return False
    has_keyword = any(keyword.lower() in lowered for keyword in QUANTITATIVE_KEYWORDS)
    has_unit_or_percent = bool(
        re.search(r"[-+]?\d+(?:\.\d+)?\s?%", text)
        or re.search(r"[-+]?\d+(?:\.\d+)?\s?(?:원|만원|억원|조원|달러|usd|krw|개|건|명|회|배)", text, re.IGNORECASE)
    )
    return has_keyword or has_unit_or_percent


def split_candidate_sentences(text: str) -> list[str]:
    """Split markdown-ish text into candidate claim sentences."""
    candidates: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*+>\s#`]+", "", line).strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.!?。！？])\s+|[;；]", line)
        for part in parts:
            part = part.strip()
            if part:
                candidates.append(part)
    return candidates


def extract_quantitative_sentences(text: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for sentence in split_candidate_sentences(text):
        if looks_quantitative(sentence):
            key = _normalize_text(sentence)
            if key not in seen:
                seen.add(key)
                results.append(sentence)
    return results


def _artifact_exists(run_dir: Path, artifact_name: str) -> bool:
    artifact_path = Path(artifact_name)
    if artifact_path.is_absolute():
        return artifact_path.exists() and artifact_path.is_file()
    return (run_dir / artifact_path).exists() and (run_dir / artifact_path).is_file()


def _source_artifact_status(run_dir: Path, artifacts: list[Any]) -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for raw in artifacts:
        name = str(raw)
        if _artifact_exists(run_dir, name):
            present.append(name)
        else:
            missing.append(name)
    return present, missing


def _is_quantitative_claim(claim: dict[str, Any]) -> bool:
    claim_type = str(claim.get("type", "")).lower()
    return claim_type in QUANTITATIVE_CLAIM_TYPES or looks_quantitative(str(claim.get("text", "")))


def _claim_covers_sentence(claim: dict[str, Any], sentence: str) -> bool:
    claim_text = str(claim.get("text", ""))
    if not claim_text:
        return False
    norm_claim = _normalize_text(claim_text)
    norm_sentence = _normalize_text(sentence)
    if not norm_claim or not norm_sentence:
        return False
    if norm_claim in norm_sentence or norm_sentence in norm_claim:
        return True

    # Conservative token overlap fallback. This is intentionally not strong
    # enough to silently approve weak manifests; it only reduces brittle exact
    # matching for near-identical wording.
    claim_tokens = set(re.findall(r"[\w가-힣.%+-]+", claim_text.lower()))
    sentence_tokens = set(re.findall(r"[\w가-힣.%+-]+", sentence.lower()))
    numeric_tokens = {token for token in sentence_tokens if any(ch.isdigit() for ch in token)}
    if not numeric_tokens or not numeric_tokens.issubset(claim_tokens):
        return False
    overlap = len(claim_tokens & sentence_tokens) / max(1, len(sentence_tokens))
    return overlap >= 0.6


def empty_claim_manifest(*, source_document: str | None = None) -> dict[str, Any]:
    """Return a valid empty manifest skeleton for manual population."""
    return {
        "artifact_type": "claim_manifest",
        "schema_version": CLAIM_MANIFEST_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_document": source_document,
        "claims": [],
    }


def validate_manifest_obj(
    manifest: dict[str, Any],
    *,
    run_dir: str | Path,
    output_text: str | None = None,
    strict_output_coverage: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    claim_results: list[dict[str, Any]] = []

    run_root = Path(run_dir)

    if manifest.get("artifact_type") != "claim_manifest":
        errors.append("Manifest artifact_type must be 'claim_manifest'.")
    if manifest.get("schema_version") not in {CLAIM_MANIFEST_SCHEMA_VERSION, None}:
        warnings.append(f"Unknown claim manifest schema_version: {manifest.get('schema_version')}")

    raw_claims = manifest.get("claims")
    if not isinstance(raw_claims, list):
        errors.append("Manifest claims must be a list.")
        raw_claims = []

    seen_ids: set[str] = set()
    quantitative_claims = 0
    verified_quantitative_claims = 0

    for index, raw_claim in enumerate(raw_claims):
        prefix = f"claims[{index}]"
        if not isinstance(raw_claim, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        claim = raw_claim
        missing_fields = sorted(REQUIRED_CLAIM_FIELDS - set(claim))
        if missing_fields:
            errors.append(f"{prefix} missing required fields: {', '.join(missing_fields)}")

        claim_id = str(claim.get("claim_id", "")).strip()
        if not claim_id:
            errors.append(f"{prefix}.claim_id must be non-empty.")
        elif claim_id in seen_ids:
            errors.append(f"Duplicate claim_id: {claim_id}")
        else:
            seen_ids.add(claim_id)

        text = str(claim.get("text", "")).strip()
        if not text:
            errors.append(f"{prefix}.text must be non-empty.")

        source_artifacts = claim.get("source_artifacts")
        if not isinstance(source_artifacts, list) or not source_artifacts:
            errors.append(f"{prefix}.source_artifacts must be a non-empty list.")
            source_artifacts = []
        present, missing = _source_artifact_status(run_root, source_artifacts)
        if missing:
            errors.append(f"{prefix}.source_artifacts missing files: {', '.join(missing)}")

        is_quant = _is_quantitative_claim(claim)
        if is_quant:
            quantitative_claims += 1
            if claim.get("verified") is not True:
                errors.append(f"{prefix}.verified must be true for quantitative claims.")
            else:
                verified_quantitative_claims += 1
            if not str(claim.get("calculation", "")).strip():
                errors.append(f"{prefix}.calculation is required for quantitative claims.")
            if not present:
                errors.append(f"{prefix} has no existing evidence artifact for a quantitative claim.")
        else:
            if claim.get("verified") is not True:
                warnings.append(f"{prefix}.verified is not true. Non-quantitative claim will not satisfy strict evidence gates.")

        claim_results.append(
            {
                "claim_id": claim_id or f"index_{index}",
                "type": claim.get("type"),
                "quantitative": is_quant,
                "verified": claim.get("verified") is True,
                "source_artifacts_present": present,
                "source_artifacts_missing": missing,
            }
        )

    uncovered_sentences: list[str] = []
    quantitative_sentences: list[str] = []
    if output_text is not None:
        quantitative_sentences = extract_quantitative_sentences(output_text)
        quantitative_claim_objs = [claim for claim in raw_claims if isinstance(claim, dict) and _is_quantitative_claim(claim)]
        for sentence in quantitative_sentences:
            if not any(_claim_covers_sentence(claim, sentence) for claim in quantitative_claim_objs):
                uncovered_sentences.append(sentence)
        if uncovered_sentences:
            message = f"Output has {len(uncovered_sentences)} quantitative-looking sentence(s) not covered by claim_manifest."
            if strict_output_coverage:
                errors.append(message)
            else:
                warnings.append(message)

    return {
        "artifact_type": "claim_manifest_validator_result",
        "generated_at": utc_now_iso(),
        "validator": "claim_manifest_validator",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "claim_count": len(raw_claims),
        "quantitative_claim_count": quantitative_claims,
        "verified_quantitative_claim_count": verified_quantitative_claims,
        "claim_results": claim_results,
        "output_quantitative_sentence_count": len(quantitative_sentences),
        "uncovered_output_quantitative_sentences": uncovered_sentences,
        "strict_output_coverage": strict_output_coverage,
    }


def validate_claim_manifest(
    manifest_path: str | Path,
    *,
    run_dir: str | Path | None = None,
    output_document: str | Path | None = None,
    strict_output_coverage: bool = False,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    manifest = load_json(manifest_file)
    if not isinstance(manifest, dict):
        return {
            "artifact_type": "claim_manifest_validator_result",
            "generated_at": utc_now_iso(),
            "validator": "claim_manifest_validator",
            "ok": False,
            "errors": ["Manifest root must be a JSON object."],
            "warnings": [],
            "claim_count": 0,
            "quantitative_claim_count": 0,
            "verified_quantitative_claim_count": 0,
            "claim_results": [],
            "output_quantitative_sentence_count": 0,
            "uncovered_output_quantitative_sentences": [],
            "strict_output_coverage": strict_output_coverage,
        }

    root = Path(run_dir) if run_dir is not None else manifest_file.parent
    output_text = None
    output_sha256 = None
    if output_document is not None:
        output_path = Path(output_document)
        output_text = output_path.read_text(encoding="utf-8")
        output_sha256 = sha256_file(output_path)

    result = validate_manifest_obj(
        manifest,
        run_dir=root,
        output_text=output_text,
        strict_output_coverage=strict_output_coverage,
    )
    result["manifest_path"] = str(manifest_file)
    result["manifest_sha256"] = sha256_file(manifest_file)
    result["run_dir"] = str(root)
    if output_document is not None:
        result["output_document"] = str(output_document)
        result["output_document_sha256"] = output_sha256
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate MCPGuardian claim_manifest.json")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Write an empty claim_manifest.json skeleton")
    init_parser.add_argument("--output", required=True)
    init_parser.add_argument("--source-document", default=None)

    validate_parser = sub.add_parser("validate", help="Validate an existing claim manifest")
    validate_parser.add_argument("--manifest", required=True)
    validate_parser.add_argument("--run-dir", default=None)
    validate_parser.add_argument("--output-document", default=None)
    validate_parser.add_argument("--strict-output-coverage", action="store_true")
    validate_parser.add_argument("--result-out", default=None, help="Write validator result JSON. Defaults to stdout only.")
    validate_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "init":
        output = Path(args.output)
        locked_atomic_write_json(output, empty_claim_manifest(source_document=args.source_document))
        print(str(output))
        return 0

    result = validate_claim_manifest(
        args.manifest,
        run_dir=args.run_dir,
        output_document=args.output_document,
        strict_output_coverage=args.strict_output_coverage,
    )
    if args.result_out:
        locked_atomic_write_json(args.result_out, result)
    if args.json or not args.result_out:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"ok: {result['ok']}")
        print(f"errors: {len(result['errors'])}")
        print(f"warnings: {len(result['warnings'])}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
