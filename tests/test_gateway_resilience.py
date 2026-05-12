import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.backend_client import AsyncStdioBackendClient, BackendConfig, BackendTimeout
from guardian.gateway_router import GatewayConfig, GatewayRouter
from guardian.resilience import CircuitOpenError

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent


class BackendResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_retry_is_disabled_by_default(self):
        flag_dir = tempfile.TemporaryDirectory()
        self.addCleanup(flag_dir.cleanup)
        flag = Path(flag_dir.name) / "fail_once.flag"
        flag.write_text("1", encoding="utf-8")
        client = AsyncStdioBackendClient(
            BackendConfig(
                name="fake",
                command=sys.executable,
                args=[str(TESTS / "fake_mcp_backend.py")],
                env={"FAKE_FAIL_ONCE_FILE": str(flag)},
                tool_prefix="fake",
                timeout_seconds=2,
                max_retries=1,
                retry_tool_calls=False,
                safe_retry_tools=["crash_once_then_echo"],
            )
        )
        try:
            await client.initialize()
            with self.assertRaises(Exception):
                await client.call_tool("crash_once_then_echo", {"message": "hello"})
            self.assertEqual(client.metrics.retries, 0)
        finally:
            await client.stop()

    async def test_safe_retry_tool_call_restarts_and_succeeds(self):
        flag_dir = tempfile.TemporaryDirectory()
        self.addCleanup(flag_dir.cleanup)
        flag = Path(flag_dir.name) / "fail_once.flag"
        flag.write_text("1", encoding="utf-8")
        client = AsyncStdioBackendClient(
            BackendConfig(
                name="fake",
                command=sys.executable,
                args=[str(TESTS / "fake_mcp_backend.py")],
                env={"FAKE_FAIL_ONCE_FILE": str(flag)},
                tool_prefix="fake",
                timeout_seconds=2,
                max_retries=1,
                retry_delay_base_seconds=0,
                retry_tool_calls=True,
                safe_retry_tools=["crash_once_then_echo"],
                circuit_breaker_failure_threshold=5,
            )
        )
        try:
            result = await client.call_tool("crash_once_then_echo", {"message": "hello"})
            self.assertIn("hello", result["content"][0]["text"])
            self.assertGreaterEqual(client.metrics.restarts, 1)
            self.assertEqual(client.metrics.retries, 1)
        finally:
            await client.stop()

    async def test_circuit_opens_after_repeated_timeouts(self):
        client = AsyncStdioBackendClient(
            BackendConfig(
                name="fake",
                command=sys.executable,
                args=[str(TESTS / "fake_mcp_backend.py")],
                tool_prefix="fake",
                timeout_seconds=0.05,
                max_retries=0,
                retry_tool_calls=True,
                safe_retry_tools=["sleep"],
                circuit_breaker_failure_threshold=2,
                circuit_breaker_recovery_seconds=999,
            )
        )
        try:
            with self.assertRaises(BackendTimeout):
                await client.call_tool("sleep", {"seconds": 0.2})
            with self.assertRaises(BackendTimeout):
                await client.call_tool("sleep", {"seconds": 0.2})
            with self.assertRaises(CircuitOpenError):
                await client.call_tool("echo", {"message": "blocked by circuit"})
            self.assertEqual(client.health()["circuit_breaker"]["state"], "open")
        finally:
            await client.stop()


class GatewayResilienceStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        self.config_path = self.root / "config" / "gateway_config.json"
        locked_atomic_write_json(
            self.config_path,
            {
                "schema_version": "1.0",
                "enable_rule_mutation": False,
                "paths": {
                    "runs_dir": "runs",
                    "active_rules": "config/active_rules.json",
                    "pending_rules": "config/pending_rules.json",
                    "rule_history": "config/rule_history.jsonl",
                },
                "allowed_roots": ["."],
                "gateway": {"tool_separator": "__"},
                "backends": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(TESTS / "fake_mcp_backend.py")],
                        "tool_prefix": "fake",
                        "timeout_seconds": 5,
                        "resilience": {"max_retries": 1, "retry_delay_base_seconds": 0},
                    }
                },
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    async def test_gateway_status_tool_reports_backend_health(self):
        cfg = GatewayConfig.load(root=self.root, config_path=self.config_path)
        router = GatewayRouter(cfg)
        self.addAsyncCleanup(router.shutdown)
        await router.call_tool("fake__echo", {"message": "hello"})
        status = await router.call_tool("mcpguardian_gateway_status", {})
        payload = json.loads(status["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertIn("fake", payload["backends"])
        self.assertIn("circuit_breaker", payload["backends"]["fake"])
        self.assertIn("metrics", payload["backends"]["fake"])


if __name__ == "__main__":
    unittest.main()
