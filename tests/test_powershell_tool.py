import json
import shutil
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.gateway_router import GatewayConfig, GatewayRouter

ROOT = Path(__file__).resolve().parents[1]


def payload(result):
    return json.loads(result["content"][0]["text"])


class NativePowerShellToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        self.config_path = self.root / "config" / "gateway_config.json"
        locked_atomic_write_json(self.config_path, {"schema_version": "1.0", "paths": {"runs_dir": "runs", "active_rules": "config/active_rules.json", "pending_rules": "config/pending_rules.json", "rule_history": "config/rule_history.jsonl"}, "allowed_roots": ["."], "gateway": {"tool_separator": "__"}, "backends": {}})

    def tearDown(self):
        self.tmp.cleanup()

    async def _router(self):
        router = GatewayRouter(GatewayConfig.load(root=self.root, config_path=self.config_path))
        self.addAsyncCleanup(router.shutdown)
        return router

    async def test_command_stdout_capture(self):
        router = await self._router()
        result = payload(await router.call_tool("guardian_powershell", {"command": "echo hello-native", "timeout": 5, "working_directory": str(self.root)}))
        self.assertTrue(result["ok"], result)
        self.assertIn("hello-native", result["data"]["stdout"])
        self.assertEqual(result["data"]["return_code"], 0)

    async def test_timeout(self):
        router = await self._router()
        result = payload(await router.call_tool("guardian_powershell", {"command": "python -c \"import time; time.sleep(2)\"", "timeout": 1, "working_directory": str(self.root)}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "COMMAND_TIMEOUT")

    async def test_dangerous_command_blocked(self):
        router = await self._router()
        result = payload(await router.call_tool("guardian_powershell", {"command": "Format-Disk -Number 0", "timeout": 5}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "DANGEROUS_COMMAND_BLOCKED")

    async def test_active_rule_command_regex_blocks(self):
        locked_atomic_write_json(
            self.root / "config" / "active_rules.json",
            {"rules": [{"id": "native.block.shutdown.v1", "status": "active", "scope": "gateway", "task_types": ["mcp_tool_call"], "severity": "critical", "enforcement": "block", "condition": {"mcp_backend": "native", "mcp_tool_name": "guardian_powershell", "command_regex_any": ["shutdown"]}, "required_artifacts": [], "message": "shutdown blocked"}]},
        )
        router = await self._router()
        result = await router.call_tool("guardian_powershell", {"command": "shutdown /s /t 0"})
        self.assertTrue(result.get("isError"))
        self.assertIn("POLICY_BLOCKED", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
