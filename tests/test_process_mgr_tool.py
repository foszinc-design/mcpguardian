import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.gateway_router import GatewayConfig, GatewayRouter

ROOT = Path(__file__).resolve().parents[1]


def payload(result):
    return json.loads(result["content"][0]["text"])


class NativeProcessManagerToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        self.config_path = self.root / "config" / "gateway_config.json"
        locked_atomic_write_json(self.config_path, {"schema_version": "1.0", "paths": {"runs_dir": "runs", "active_rules": "config/active_rules.json", "pending_rules": "config/pending_rules.json", "rule_history": "config/rule_history.jsonl"}, "allowed_roots": [".", "/opt/pyvenv/bin"], "gateway": {"tool_separator": "__"}, "backends": {}})

    def tearDown(self):
        self.tmp.cleanup()

    async def _router(self):
        router = GatewayRouter(GatewayConfig.load(root=self.root, config_path=self.config_path))
        self.addAsyncCleanup(router.shutdown)
        return router

    async def test_start_list_and_kill_process(self):
        router = await self._router()
        started = payload(await router.call_tool("guardian_start_process", {"command": sys.executable, "args": ["-c", "import time; time.sleep(30)"], "detached": True}))
        self.assertTrue(started["ok"], started)
        pid = started["data"]["pid"]
        listed = payload(await router.call_tool("guardian_list_processes", {"query": str(pid), "max_results": 20}))
        self.assertTrue(listed["ok"])
        killed = payload(await router.call_tool("guardian_kill_process", {"pid": pid, "tree": True}))
        self.assertTrue(killed["ok"], killed)


if __name__ == "__main__":
    unittest.main()
