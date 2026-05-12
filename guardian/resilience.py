"""Resilience primitives for MCPGuardian Gateway Phase 6.

This module is intentionally transport-agnostic. It contains no MCP-specific
logic, which keeps circuit breaker and retry behavior testable without spawning
backend processes.
"""
from __future__ import annotations

import asyncio
import math
import os
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 3.0
    backoff_multiplier: float = 2.0

    def delay_for_attempt(self, attempt_index: int) -> float:
        """Return delay before the next retry.

        attempt_index is 1-based for the failed attempt. The first retry delay
        therefore uses attempt_index=1.
        """
        raw = self.base_delay_seconds * (self.backoff_multiplier ** max(0, attempt_index - 1))
        if not math.isfinite(raw) or raw < 0:
            raw = self.base_delay_seconds
        return min(max(raw, 0.0), max(self.max_delay_seconds, 0.0))


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    recovery_seconds: float = 30.0
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at_monotonic: float | None = None
    last_failure: str | None = None

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.HALF_OPEN:
            return True
        if self.opened_at_monotonic is None:
            return False
        if time.monotonic() - self.opened_at_monotonic >= self.recovery_seconds:
            self.state = CircuitState.HALF_OPEN
            return True
        return False

    def assert_request_allowed(self) -> None:
        if not self.allow_request():
            raise CircuitOpenError(f"Circuit is open for backend {self.name}")

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at_monotonic = None
        self.last_failure = None

    def record_failure(self, reason: str) -> None:
        self.consecutive_failures += 1
        self.last_failure = reason
        if self.consecutive_failures >= max(1, self.failure_threshold):
            self.state = CircuitState.OPEN
            self.opened_at_monotonic = time.monotonic()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "recovery_seconds": self.recovery_seconds,
            "last_failure": self.last_failure,
        }


@dataclass
class BackendMetrics:
    starts: int = 0
    restarts: int = 0
    requests_total: int = 0
    requests_ok: int = 0
    requests_failed: int = 0
    timeouts: int = 0
    retries: int = 0
    stdout_messages: int = 0
    stdout_bytes: int = 0
    stderr_lines: int = 0
    stderr_bytes: int = 0
    pending_high_watermark: int = 0
    last_error: str | None = None
    last_restart_reason: str | None = None

    def observe_pending(self, pending_count: int) -> None:
        self.pending_high_watermark = max(self.pending_high_watermark, pending_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "starts": self.starts,
            "restarts": self.restarts,
            "requests_total": self.requests_total,
            "requests_ok": self.requests_ok,
            "requests_failed": self.requests_failed,
            "timeouts": self.timeouts,
            "retries": self.retries,
            "stdout_messages": self.stdout_messages,
            "stdout_bytes": self.stdout_bytes,
            "stderr_lines": self.stderr_lines,
            "stderr_bytes": self.stderr_bytes,
            "pending_high_watermark": self.pending_high_watermark,
            "last_error": self.last_error,
            "last_restart_reason": self.last_restart_reason,
        }


async def terminate_process_tree(proc: asyncio.subprocess.Process, *, timeout_seconds: float = 3.0, kill_tree: bool = True) -> None:
    """Terminate a process and, when psutil is available, its descendants.

    Windows process-tree cleanup is best-effort here. If psutil is unavailable,
    this falls back to terminate/kill on the direct child process only.
    """
    if proc.returncode is not None:
        return
    if kill_tree:
        try:
            import psutil  # type: ignore

            root = psutil.Process(proc.pid)
            children = root.children(recursive=True)
            for child in children:
                with _suppress_process_errors():
                    child.terminate()
            with _suppress_process_errors():
                root.terminate()
            gone, alive = psutil.wait_procs([*children, root], timeout=timeout_seconds)
            for item in alive:
                with _suppress_process_errors():
                    item.kill()
            await _wait_asyncio_process(proc, timeout_seconds=timeout_seconds)
            return
        except Exception:
            # Fall back below. Cleanup must not crash the gateway shutdown path.
            pass
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
    except Exception:
        with _suppress_process_errors():
            proc.kill()
        await _wait_asyncio_process(proc, timeout_seconds=timeout_seconds)


async def _wait_asyncio_process(proc: asyncio.subprocess.Process, *, timeout_seconds: float) -> None:
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
    except Exception:
        pass


class _suppress_process_errors:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc_type in {ProcessLookupError, PermissionError, OSError}
