"""MCP Tool Gateway routing and policy enforcement for Phase 5B."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .atomic_io import load_json
from .backend_client import AsyncStdioBackendClient, BackendConfig, BackendError
from .resilience import CircuitOpenError
from .mcp_tools import GuardianPaths, error_envelope, ok_envelope
from .path_policy import PathPolicyError
from .preflight_gate import evaluate_gate, load_active_rules
from .schemas import Decision, GateRequest, InputFile
from .structured_trace import RunContext


BLOCKING_DECISIONS = {Decision.BLOCK.value, Decision.REQUIRE_ARTIFACT.value}
WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
PATHISH_KEYS = {"path", "file", "filename", "input", "input_path", "output", "output_path", "directory", "dir", "cwd", "root"}


@dataclass(frozen=True)
class GatewayConfig:
    paths: GuardianPaths
    backends: list[BackendConfig]
    tool_separator: str = "__"
    default_tool_timeout_seconds: float = 30.0

    @classmethod
    def load(cls, *, root: str | Path | None = None, config_path: str | Path | None = None) -> "GatewayConfig":
        paths = GuardianPaths.load(root=root, config_path=config_path)
        cfg = load_json(paths.config_path, default={}) if paths.config_path and paths.config_path.exists() else {}
        gateway = cfg.get("gateway", {}) if isinstance(cfg, dict) else {}
        raw_backends = cfg.get("backends", {}) if isinstance(cfg, dict) else {}
        backends = [BackendConfig.from_dict(name, obj) for name, obj in raw_backends.items()]
        return cls(
            paths=paths,
            backends=backends,
            tool_separator=str(gateway.get("tool_separator", "__")),
            default_tool_timeout_seconds=float(gateway.get("default_tool_timeout_seconds", 30.0)),
        )


class GatewayRouter:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.clients: dict[str, AsyncStdioBackendClient] = {
            backend.name: AsyncStdioBackendClient(backend) for backend in config.backends if not backend.disabled
        }
        self.tool_index: dict[str, tuple[str, str]] = {}
        self.internal_tools = {"mcpguardian_gateway_status"}
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        for name, client in self.clients.items():
            await client.initialize()
            for tool in await client.list_tools():
                backend_tool_name = str(tool["name"])
                public_name = self.public_tool_name(client.config, backend_tool_name)
                if public_name in self.tool_index:
                    raise ValueError(f"Duplicate public tool name: {public_name}")
                self.tool_index[public_name] = (name, backend_tool_name)
        self._initialized = True

    def public_tool_name(self, backend: BackendConfig, tool_name: str) -> str:
        return f"{backend.tool_prefix}{self.config.tool_separator}{tool_name}"

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.initialize()
        out: list[dict[str, Any]] = [
            {
                "name": "mcpguardian_gateway_status",
                "description": "Return MCPGuardian Gateway backend health, circuit breaker, retry, and pressure metrics.",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
        for backend_name, client in self.clients.items():
            for tool in await client.list_tools():
                cloned = dict(tool)
                original_name = str(tool["name"])
                public_name = self.public_tool_name(client.config, original_name)
                cloned["name"] = public_name
                desc = str(cloned.get("description") or "")
                cloned["description"] = f"[MCPGuardian routed: {backend_name}/{original_name}] {desc}".strip()
                out.append(cloned)
        return sorted(out, key=lambda item: str(item.get("name")))

    async def call_tool(self, public_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.initialize()
        if public_name == "mcpguardian_gateway_status":
            return self.gateway_status_tool_result()
        if public_name not in self.tool_index:
            return self._mcp_tool_error(f"Unknown gateway tool: {public_name}", code="UNKNOWN_TOOL")
        backend_name, backend_tool_name = self.tool_index[public_name]
        client = self.clients[backend_name]
        arguments = arguments or {}
        run = RunContext(self.config.paths.runs_dir)
        writer = run.writer()
        requested_action = f"mcp backend={backend_name} tool={backend_tool_name} args={json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
        try:
            self._enforce_argument_paths(arguments)
            writer.run_started(task_type="mcp_tool_call", requested_action=requested_action, input_files=[])
            decision = self._evaluate_policy(run, backend_name=backend_name, tool_name=backend_tool_name, requested_action=requested_action)
            writer.preflight_evaluated(decision.to_dict())
            if decision.decision in BLOCKING_DECISIONS:
                writer.run_finished(status="blocked", summary=f"gateway blocked {backend_name}/{backend_tool_name}")
                return self._mcp_tool_error(
                    f"MCPGuardian blocked tool call {backend_name}/{backend_tool_name}: {decision.decision}",
                    code="POLICY_BLOCKED",
                    data={"run_id": run.run_id, "decision": decision.to_dict()},
                )
            writer.emit("backend_tool_call_started", backend=backend_name, tool=backend_tool_name)
            result = await client.call_tool(backend_tool_name, arguments)
            writer.emit("backend_tool_call_finished", backend=backend_name, tool=backend_tool_name)
            writer.run_finished(status="ok", summary=f"gateway routed {backend_name}/{backend_tool_name}")
            return result
        except PathPolicyError as exc:
            writer.run_finished(status="blocked", summary=str(exc))
            return self._mcp_tool_error(str(exc), code="PATH_POLICY_DENIED", data={"run_id": run.run_id})
        except CircuitOpenError as exc:
            writer.emit("backend_circuit_open", backend=backend_name, tool=backend_tool_name, error=str(exc))
            writer.run_finished(status="blocked", summary=str(exc))
            return self._mcp_tool_error(str(exc), code="CIRCUIT_OPEN", data={"run_id": run.run_id, "backend": backend_name})
        except Exception as exc:
            writer.run_finished(status="error", summary=str(exc))
            return self._mcp_tool_error(str(exc), code="BACKEND_CALL_FAILED", data={"run_id": run.run_id})

    def _evaluate_policy(self, run: RunContext, *, backend_name: str, tool_name: str, requested_action: str):
        rules = load_active_rules(self.config.paths.active_rules)
        request = GateRequest(
            task_type="mcp_tool_call",
            requested_action=requested_action,
            input_files=[],
            run_dir=str(run.run_dir),
            existing_artifacts=[],
            context={"mcp_backend": backend_name, "mcp_tool_name": tool_name},
        )
        return evaluate_gate(request, rules)

    def _enforce_argument_paths(self, value: Any) -> None:
        for raw in collect_pathish_strings(value):
            self.config.paths.policy().resolve_allowed(raw, must_exist=False)

    @staticmethod
    def _mcp_tool_error(message: str, *, code: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"ok": False, "error_code": code, "message": message}
        if data:
            payload.update(data)
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                }
            ],
        }

    def backend_status(self) -> dict[str, Any]:
        return {name: client.health() for name, client in sorted(self.clients.items())}

    def gateway_status_tool_result(self) -> dict[str, Any]:
        payload = {"ok": True, "backends": self.backend_status()}
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                }
            ]
        }

    async def shutdown(self) -> None:
        await asyncio.gather(*(client.stop() for client in self.clients.values()), return_exceptions=True)


def collect_pathish_strings(value: Any, *, parent_key: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_s = str(key).lower()
            if isinstance(item, str) and _looks_pathish(key_s, item):
                out.append(item)
            else:
                out.extend(collect_pathish_strings(item, parent_key=key_s))
    elif isinstance(value, list):
        for item in value:
            out.extend(collect_pathish_strings(item, parent_key=parent_key))
    elif isinstance(value, str) and _looks_pathish(parent_key, value):
        out.append(value)
    return out


def _looks_pathish(key: str, text: str) -> bool:
    stripped = text.strip()
    if not stripped or "\n" in stripped:
        return False
    key_hit = any(token == key or key.endswith("_" + token) or key.endswith(token + "_path") for token in PATHISH_KEYS)
    absolute_hit = Path(stripped).is_absolute() or bool(WINDOWS_ABSOLUTE_RE.match(stripped))
    separator_hit = ("/" in stripped or "\\" in stripped) and not stripped.startswith("http")
    extension_hit = bool(re.search(r"\.(xlsx|xlsm|csv|json|md|txt|py|js|ts|docx|pdf)$", stripped, flags=re.I))
    return bool(key_hit and (absolute_hit or separator_hit or extension_hit)) or absolute_hit
