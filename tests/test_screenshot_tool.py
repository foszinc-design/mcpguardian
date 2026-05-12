import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from guardian.atomic_io import locked_atomic_write_json
from guardian.gateway_router import GatewayConfig, GatewayRouter

ROOT = Path(__file__).resolve().parents[1]


def payload(result):
    return json.loads(result["content"][0]["text"])


class FakeImage:
    def save(self, path, format=None):
        Path(path).write_bytes(b"PNG")


class NativeScreenshotToolTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_screenshot_file_created_with_mocked_imagegrab(self):
        router = await self._router()
        out = self.root / "shot.png"
        with patch("PIL.ImageGrab.grab", return_value=FakeImage()):
            result = payload(await router.call_tool("guardian_screenshot", {"output_path": str(out)}))
        self.assertTrue(result["ok"], result)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
