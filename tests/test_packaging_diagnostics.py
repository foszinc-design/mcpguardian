import json
import shutil
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.packaging.config_migration import migrate_claude_desktop_config
from guardian.packaging.diagnostics import run_diagnostics

ROOT = Path(__file__).resolve().parents[1]


class PackagingDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        self.guardian_config = self.root / "config" / "gateway_config.json"
        locked_atomic_write_json(self.guardian_config, {"paths": {"runs_dir": "runs", "active_rules": "config/active_rules.json", "pending_rules": "config/pending_rules.json", "rule_history": "config/rule_history.jsonl"}, "allowed_roots": ["."], "backends": {}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_claude_config_preserves_unrelated_and_removes_replaced_servers(self):
        config = self.root / "claude_desktop_config.json"
        locked_atomic_write_json(config, {"mcpServers": {"filesystem": {"command": "node"}, "windows-mcp": {"command": "node"}, "other": {"command": "ok"}}})
        result = migrate_claude_desktop_config(config_path=config, python_exe="C:/Python311/python.exe", gateway_script=self.root / "guardian_gateway.py", root=self.root, guardian_config=self.guardian_config)
        self.assertTrue(result["ok"])
        self.assertEqual(set(result["removed_servers"]), {"filesystem", "windows-mcp"})
        updated = json.loads(config.read_text(encoding="utf-8"))
        self.assertIn("mcp-guardian", updated["mcpServers"])
        self.assertIn("other", updated["mcpServers"])
        self.assertNotIn("filesystem", updated["mcpServers"])
        self.assertTrue(Path(result["backup_path"]).exists())

    def test_diagnostics_reports_ok_for_minimal_config(self):
        result = run_diagnostics(root=self.root, config_path=self.guardian_config)
        self.assertTrue(result["ok"], result)
        names = [item["name"] for item in result["checks"]]
        self.assertIn("active_rules.load", names)
        self.assertIn("path_policy.load", names)


if __name__ == "__main__":
    unittest.main()
