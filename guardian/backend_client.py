"""Async stdio MCP backend client for Guardian Gateway Phase 6.

Phase 6 adds resilience around the Phase 5B transport:
- circuit breaker
- conservative retry policy
- backend restart on transport failure / timeout
- best-effort process-tree cleanup
- backend health and pressure metrics

Important safety boundary: tool-call retry is disabled by default because a timed
out tool may still have performed side effects inside the backend process.
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gateway_protocol import encode_message, make_notification, make_request, parse_message
from .resilience import BackendMetrics, CircuitBreaker, CircuitOpenError, RetryPolicy, terminate_process_tree


class BackendError(RuntimeError):
    pass


class BackendTimeout(BackendError):
    pass


class BackendCrashed(BackendError):
    pass


class BackendApplicationError(BackendError):
    pass


@dataclass(frozen=True)
class BackendConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    tool_prefix: str | None = None
    timeout_seconds: float = 30.0
    disabled: bool = False
    max_retries: int = 0
    retry_delay_base_seconds: float = 0.25
    retry_delay_max_seconds: float = 3.0
    retry_backoff_multiplier: float = 2.0
    retry_tool_calls: bool = False
    safe_retry_tools: list[str] = field(default_factory=list)
    restart_on_failure: bool = True
    restart_on_timeout: bool = True
    kill_process_tree: bool = True
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_recovery_seconds: float = 30.0

    @classmethod
    def from_dict(cls, name: str, obj: dict[str, Any]) -> "BackendConfig":
        if not obj.get("command"):
            raise ValueError(f"Backend {name} requires command")
        resilience = obj.get("resilience", {}) if isinstance(obj.get("resilience", {}), dict) else {}
        return cls(
            name=name,
            command=str(obj["command"]),
            args=[str(item) for item in obj.get("args", [])],
            env={str(k): str(v) for k, v in dict(obj.get("env", {})).items()},
            cwd=str(obj["cwd"]) if obj.get("cwd") else None,
            tool_prefix=str(obj.get("tool_prefix") or name),
            timeout_seconds=float(obj.get("timeout_seconds", 30.0)),
            disabled=bool(obj.get("disabled", False)),
            max_retries=int(resilience.get("max_retries", obj.get("max_retries", 0))),
            retry_delay_base_seconds=float(resilience.get("retry_delay_base_seconds", obj.get("retry_delay_base_seconds", 0.25))),
            retry_delay_max_seconds=float(resilience.get("retry_delay_max_seconds", obj.get("retry_delay_max_seconds", 3.0))),
            retry_backoff_multiplier=float(resilience.get("retry_backoff_multiplier", obj.get("retry_backoff_multiplier", 2.0))),
            retry_tool_calls=bool(resilience.get("retry_tool_calls", obj.get("retry_tool_calls", False))),
            safe_retry_tools=[str(item) for item in resilience.get("safe_retry_tools", obj.get("safe_retry_tools", []))],
            restart_on_failure=bool(resilience.get("restart_on_failure", obj.get("restart_on_failure", True))),
            restart_on_timeout=bool(resilience.get("restart_on_timeout", obj.get("restart_on_timeout", True))),
            kill_process_tree=bool(resilience.get("kill_process_tree", obj.get("kill_process_tree", True))),
            circuit_breaker_failure_threshold=int(resilience.get("circuit_breaker_failure_threshold", obj.get("circuit_breaker_failure_threshold", 3))),
            circuit_breaker_recovery_seconds=float(resilience.get("circuit_breaker_recovery_seconds", obj.get("circuit_breaker_recovery_seconds", 30.0))),
        )

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_retries=max(0, self.max_retries),
            base_delay_seconds=max(0.0, self.retry_delay_base_seconds),
            max_delay_seconds=max(0.0, self.retry_delay_max_seconds),
            backoff_multiplier=max(1.0, self.retry_backoff_multiplier),
        )


class AsyncStdioBackendClient:
    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[Any, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._initialized = False
        self.stderr_lines: list[str] = []
        self.tools: list[dict[str, Any]] = []
        self.metrics = BackendMetrics()
        self.circuit_breaker = CircuitBreaker(
            name=config.name,
            failure_threshold=config.circuit_breaker_failure_threshold,
            recovery_seconds=config.circuit_breaker_recovery_seconds,
        )

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if self.config.disabled:
            raise BackendError(f"Backend is disabled: {self.config.name}")
        if self.is_running:
            return
        env = os.environ.copy()
        env.update(self.config.env)
        self.process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.cwd,
            env=env,
        )
        self.metrics.starts += 1
        self._reader_task = asyncio.create_task(self._read_stdout_loop(), name=f"mcp-backend-stdout-{self.config.name}")
        self._stderr_task = asyncio.create_task(self._read_stderr_loop(), name=f"mcp-backend-stderr-{self.config.name}")

    async def initialize(self) -> dict[str, Any]:
        if self._initialized and self.is_running:
            return {"already_initialized": True}
        await self.start()
        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcpguardian-gateway", "version": "6"},
            },
            retryable=True,
            ensure_initialized=False,
        )
        await self.notify("notifications/initialized", {})
        listed = await self.request("tools/list", {}, retryable=True, ensure_initialized=False)
        tools = listed.get("tools", []) if isinstance(listed, dict) else []
        self.tools = [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]
        self._initialized = True
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.initialize()
        if not self.tools:
            listed = await self.request("tools/list", {}, retryable=True)
            tools = listed.get("tools", []) if isinstance(listed, dict) else []
            self.tools = [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.initialize()
        retryable = self._tool_call_retryable(name)
        result = await self.request("tools/call", {"name": name, "arguments": arguments or {}}, retryable=retryable)
        if not isinstance(result, dict):
            raise BackendError(f"Backend {self.config.name} returned non-object tool result")
        return result

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retryable: bool = True,
        ensure_initialized: bool = True,
    ) -> dict[str, Any]:
        if ensure_initialized and method not in {"initialize", "notifications/initialized"}:
            await self.initialize()
        policy = self.config.retry_policy()
        max_attempts = 1 + (policy.max_retries if retryable else 0)
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            self.circuit_breaker.assert_request_allowed()
            try:
                self.metrics.requests_total += 1
                result = await self._request_once(method, params or {})
                self.metrics.requests_ok += 1
                if method == "tools/call":
                    self.circuit_breaker.record_success()
                return result
            except BackendTimeout as exc:
                self.metrics.timeouts += 1
                last_exc = exc
                self._record_request_failure(exc)
                if self.config.restart_on_timeout:
                    await self.restart(reason=f"timeout:{method}")
            except BackendApplicationError as exc:
                last_exc = exc
                self.metrics.requests_failed += 1
                self.metrics.last_error = str(exc)
            except (BackendCrashed, BrokenPipeError, ConnectionResetError, OSError, BackendError) as exc:
                last_exc = exc
                self._record_request_failure(exc)
                if self.config.restart_on_failure and self._is_transport_failure(exc):
                    await self.restart(reason=f"failure:{method}:{type(exc).__name__}")
            if attempt >= max_attempts:
                break
            if method == "tools/call" and self.is_running and not self._initialized:
                try:
                    await self.initialize()
                except Exception as init_exc:
                    last_exc = init_exc
            self.metrics.retries += 1
            await asyncio.sleep(policy.delay_for_attempt(attempt))
        assert last_exc is not None
        raise last_exc

    async def _request_once(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        if self.process is None or self.process.stdin is None:
            raise BackendError(f"Backend {self.config.name} stdin is unavailable")
        if self.process.returncode is not None:
            raise BackendCrashed(f"Backend {self.config.name} already exited with {self.process.returncode}")
        message_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[message_id] = fut
        self.metrics.observe_pending(len(self._pending))
        try:
            self.process.stdin.write(encode_message(make_request(message_id, method, params)))
            await self.process.stdin.drain()
        except Exception:
            self._pending.pop(message_id, None)
            raise
        try:
            timeout_seconds = max(self.config.timeout_seconds, 5.0) if method in {"initialize", "tools/list"} else self.config.timeout_seconds
            msg = await asyncio.wait_for(fut, timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise BackendTimeout(f"Backend {self.config.name} timed out on {method}") from exc
        if "error" in msg:
            error = msg.get("error") or {}
            raise BackendApplicationError(f"Backend {self.config.name} error on {method}: {error}")
        result = msg.get("result", {})
        return result if isinstance(result, dict) else {"value": result}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self.start()
        if self.process is None or self.process.stdin is None:
            raise BackendError(f"Backend {self.config.name} stdin is unavailable")
        self.process.stdin.write(encode_message(make_notification(method, params or {})))
        await self.process.stdin.drain()

    async def restart(self, *, reason: str) -> None:
        self.metrics.restarts += 1
        self.metrics.last_restart_reason = reason
        await self.stop(mark_stopped=False)
        self._initialized = False
        self.tools = []
        await self.start()

    async def stop(self, *, mark_stopped: bool = True) -> None:
        proc = self.process
        self.process = None
        self._initialized = False
        if proc and proc.returncode is None:
            if proc.stdin:
                proc.stdin.close()
            await terminate_process_tree(proc, timeout_seconds=3.0, kill_tree=self.config.kill_process_tree)
        tasks = [task for task in [self._reader_task, self._stderr_task] if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(BackendError(f"Backend stopped: {self.config.name}"))
        self._pending.clear()
        self._reader_task = None
        self._stderr_task = None

    async def _read_stdout_loop(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                self.metrics.stdout_messages += 1
                self.metrics.stdout_bytes += len(line)
                try:
                    msg = parse_message(line)
                except Exception:
                    continue
                message_id = msg.get("id")
                if message_id in self._pending:
                    fut = self._pending.pop(message_id)
                    if not fut.done():
                        fut.set_result(msg)
        finally:
            if self.process is process:
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(BackendCrashed(f"Backend stdout closed: {self.config.name}"))
                self._pending.clear()

    async def _read_stderr_loop(self) -> None:
        process = self.process
        assert process is not None and process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            self.metrics.stderr_lines += 1
            self.metrics.stderr_bytes += len(line)
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self.stderr_lines.append(text)
                if len(self.stderr_lines) > 200:
                    del self.stderr_lines[:100]

    def _tool_call_retryable(self, tool_name: str) -> bool:
        if not self.config.retry_tool_calls:
            return False
        return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in self.config.safe_retry_tools)

    def _record_request_failure(self, exc: Exception) -> None:
        self.metrics.requests_failed += 1
        self.metrics.last_error = str(exc)
        self.circuit_breaker.record_failure(str(exc))

    def _is_transport_failure(self, exc: Exception) -> bool:
        if isinstance(exc, BackendTimeout):
            return self.config.restart_on_timeout
        if isinstance(exc, BackendError) and not isinstance(exc, BackendCrashed):
            # JSON-RPC method errors are backend application errors, not transport
            # failures. Do not restart the process for these by default.
            return False
        return True

    def health(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "running": self.is_running,
            "pid": self.process.pid if self.process else None,
            "returncode": self.process.returncode if self.process else None,
            "initialized": self._initialized,
            "tool_count": len(self.tools),
            "circuit_breaker": self.circuit_breaker.to_dict(),
            "metrics": self.metrics.to_dict(),
            "stderr_tail": self.stderr_lines[-10:],
        }
