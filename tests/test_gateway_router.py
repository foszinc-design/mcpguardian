import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.gateway_router import GatewayConfig, GatewayRouter, collect_pathish_strings

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent


class GatewayRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        self.config_path = self.root / "config" / "gateway_config.json"
        self._write_config()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_config(self):
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
                    }
                },
            },
        )

    async def _router(self):
        cfg = GatewayConfig.load(root=self.root, config_path=self.config_path)
        router = GatewayRouter(cfg)
        self.addAsyncCleanup(router.shutdown)
        return router

    async def test_lists_prefixed_backend_tools(self):
        router = await self._router()
        tools = await router.list_tools()
        names = [tool["name"] for tool in tools]
        self.assertIn("fake__echo", names)
        self.assertIn("fake__read_file", names)
        self.assertIn("mcpguardian_gateway_status", names)
        routed = next(tool for tool in tools if tool["name"] == "fake__echo")
        self.assertIn("MCPGuardian routed", routed["description"])

    async def test_routes_tool_call_and_writes_trace(self):
        router = await self._router()
        result = await router.call_tool("fake__echo", {"message": "hello"})
        self.assertFalse(result.get("isError"), result)
        self.assertIn("hello", result["content"][0]["text"])
        run_dirs = list((self.root / "runs").iterdir())
        self.assertEqual(len(run_dirs), 1)
        trace = (run_dirs[0] / "trace.jsonl").read_text(encoding="utf-8")
        self.assertIn("backend_tool_call_started", trace)
        self.assertIn("preflight_evaluated", trace)

    async def test_policy_blocks_tool_call_before_backend(self):
        active = json.loads((self.root / "config" / "active_rules.json").read_text(encoding="utf-8"))
        active["rules"].append(
            {
                "id": "gateway.block.fake.echo.v1",
                "status": "active",
                "scope": "gateway",
                "task_types": ["mcp_tool_call"],
                "severity": "critical",
                "enforcement": "block",
                "condition": {"mcp_backend": "fake", "mcp_tool_name": "echo"},
                "required_artifacts": [],
                "message": "fake echo blocked",
            }
        )
        locked_atomic_write_json(self.root / "config" / "active_rules.json", active)
        router = await self._router()
        result = await router.call_tool("fake__echo", {"message": "blocked"})
        self.assertTrue(result["isError"])
        self.assertIn("POLICY_BLOCKED", result["content"][0]["text"])

    async def test_path_policy_denies_outside_argument_path(self):
        router = await self._router()
        outside = Path(tempfile.gettempdir()) / "outside_gateway.txt"
        result = await router.call_tool("fake__read_file", {"path": str(outside)})
        self.assertTrue(result["isError"])
        self.assertIn("PATH_POLICY_DENIED", result["content"][0]["text"])

    def test_collect_pathish_strings_is_key_sensitive(self):
        found = collect_pathish_strings({"message": "hello/report.xlsx", "path": "data/report.xlsx", "nested": {"output_path": "out.json"}})
        self.assertEqual(found, ["data/report.xlsx", "out.json"])


if __name__ == "__main__":
    unittest.main()
