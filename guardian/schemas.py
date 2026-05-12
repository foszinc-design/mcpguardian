"""Core schemas for MCPGuardian Phase 1.

Stdlib-only by design. These dataclasses are intentionally small and strict:
- active rules are enforcement objects
- pending rules are review objects
- trace events are immutable records once written
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class Decision(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REQUIRE_ARTIFACT = "require_artifact"
    BLOCK = "block"
    POSTCHECK_REQUIRED = "postcheck_required"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Enforcement(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REQUIRE_ARTIFACT = "require_artifact"
    BLOCK = "block"
    POSTCHECK_REQUIRED = "postcheck_required"


class RuleStatus(str, Enum):
    ACTIVE = "active"
    PROPOSED = "proposed"
    STAGED = "staged"
    REJECTED = "rejected"


Severity = Literal["low", "medium", "high", "critical"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_ext(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    return suffix if suffix.startswith(".") else f".{suffix}" if suffix else ""


@dataclass(frozen=True)
class InputFile:
    path: str
    extension: str
    exists: bool
    size_bytes: int | None = None
    sha256: str | None = None
    modified_at: str | None = None

    @classmethod
    def from_path(cls, path: str | Path, sha256: str | None = None) -> "InputFile":
        p = Path(path)
        exists = p.exists()
        stat = p.stat() if exists else None
        modified_at = None
        if stat is not None:
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        return cls(
            path=str(p),
            extension=normalize_ext(p),
            exists=exists,
            size_bytes=stat.st_size if stat is not None else None,
            sha256=sha256,
            modified_at=modified_at,
        )


@dataclass(frozen=True)
class GateRequest:
    task_type: str
    requested_action: str
    input_files: list[InputFile]
    run_dir: str
    existing_artifacts: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    id: str
    status: str
    scope: str
    task_types: list[str]
    severity: Severity
    enforcement: str
    condition: dict[str, Any]
    message: str
    required_artifacts: list[str] = field(default_factory=list)
    created_at: str | None = None
    created_by: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    rollback_of: str | None = None

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Rule":
        required = ["id", "status", "scope", "task_types", "severity", "enforcement", "condition", "message"]
        missing = [key for key in required if key not in obj]
        if missing:
            raise ValueError(f"Rule is missing required fields: {', '.join(missing)}")
        if obj["status"] != RuleStatus.ACTIVE.value:
            raise ValueError(f"Only active rules can be loaded into preflight gate: {obj.get('id')}")
        if obj["enforcement"] not in {item.value for item in Enforcement}:
            raise ValueError(f"Invalid enforcement for rule {obj.get('id')}: {obj['enforcement']}")
        return cls(
            id=obj["id"],
            status=obj["status"],
            scope=obj["scope"],
            task_types=list(obj.get("task_types", [])),
            severity=obj["severity"],
            enforcement=obj["enforcement"],
            condition=dict(obj.get("condition", {})),
            required_artifacts=list(obj.get("required_artifacts", [])),
            message=obj["message"],
            created_at=obj.get("created_at"),
            created_by=obj.get("created_by"),
            evidence_refs=list(obj.get("evidence_refs", [])),
            rollback_of=obj.get("rollback_of"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "scope": self.scope,
            "task_types": self.task_types,
            "severity": self.severity,
            "enforcement": self.enforcement,
            "condition": self.condition,
            "required_artifacts": self.required_artifacts,
            "message": self.message,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "evidence_refs": self.evidence_refs,
            "rollback_of": self.rollback_of,
        }


@dataclass(frozen=True)
class GateDecision:
    decision: str
    risk_level: str
    matched_rules: list[str]
    required_artifacts: list[str]
    messages: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "matched_rules": self.matched_rules,
            "required_artifacts": self.required_artifacts,
            "messages": self.messages,
        }


@dataclass(frozen=True)
class TraceEvent:
    event_type: str
    run_id: str
    timestamp: str
    payload: dict[str, Any]

    @classmethod
    def create(cls, event_type: str, run_id: str, **payload: Any) -> "TraceEvent":
        return cls(event_type=event_type, run_id=run_id, timestamp=utc_now_iso(), payload=payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            **self.payload,
        }
