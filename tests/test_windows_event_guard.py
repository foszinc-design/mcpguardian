import json
import shutil
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.gateway_router import GatewayConfig, GatewayRouter
from guardian.windows_event_guard import WindowsEventGuard, WindowsEventGuardConfig

ROOT = Path(__file__).resolve().parents[1]


def payload(result):
    return json.loads(result["content"][0]["text"])


class WindowsEventGuardUnitTests(unittest.TestCase):
    def test_debounce_pause_and_drive_compare(self):
        guard = WindowsEventGuard(WindowsEventGuardConfig(enabled=True, debounce_seconds=5.0, pause_backends=["native"], pause_tool_patterns=["powershell"]))
        status = guard.record_device_change(event_type="Win32_DeviceChangeEvent", device_id="USB\\VID", timestamp=100.0)
        self.assertTrue(status["paused"])
        self.assertTrue(guard.should_pause(backend="native", tool_name="guardian_powershell", now=101.0))
        self.assertFalse(guard.should_pause(backend="native", tool_name="guardian_powershell", now=106.0))
        guard.capture_drive_snapshot({"E:": "USB1"})
        diff = guard.compare_drive_snapshot({"F:": "USB1"})
        self.assertIn("E:", diff["removed"])
        self.assertIn("F:", diff["added"])


class WindowsEventGuardRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        self.config_path = self.root / "config" / "gateway_config.json"
        locked_atomic_write_json(self.config_path, {"paths": {"runs_dir": "runs", "active_rules": "config/active_rules.json", "pending_rules": "config/pending_rules.json", "rule_history": "config/rule_history.jsonl"}, "allowed_roots": ["."], "backends": {}, "windows_event_guard": {"enabled": True, "debounce_seconds": 20, "pause_backends": ["native"], "pause_tool_patterns": ["powershell"]}})

    def tearDown(self):
        self.tmp.cleanup()

    async def test_router_blocks_paused_native_tool(self):
        router = GatewayRouter(GatewayConfig.load(root=self.root, config_path=self.config_path))
        self.addAsyncCleanup(router.shutdown)
        record = payload(await router.call_tool("mcpguardian_windows_event_record", {"event_type": "USB inserted", "device_id": "USB"}))
        self.assertTrue(record["paused"])
        result = await router.call_tool("guardian_powershell", {"command": "echo should-not-run"})
        self.assertTrue(result.get("isError"))
        self.assertIn("WINDOWS_EVENT_GUARD_PAUSED", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
