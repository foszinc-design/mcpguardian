import json
import shutil
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.packaging.config_migration import (
    list_claude_config_backups,
    migrate_claude_desktop_config,
    print_guardian_config,
    rollback_claude_desktop_config,
)
from guardian.packaging.launchers import write_windows_launchers
from guardian.packaging.release_manifest import build_release_manifest

ROOT = Path(__file__).resolve().parents[1]


class PackagingRolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        self.gateway_config = self.root / "config" / "gateway_config.json"
        locked_atomic_write_json(self.gateway_config, {"paths": {"runs_dir": "runs", "active_rules": "config/active_rules.json", "pending_rules": "config/pending_rules.json", "rule_history": "config/rule_history.jsonl"}, "allowed_roots": ["."], "backends": {}})
        (self.root / "guardian_gateway.py").write_text("print('gateway')\n", encoding="utf-8")
        (self.root / "guardian_http_gateway.py").write_text("print('http')\n", encoding="utf-8")
        (self.root / "requirements.txt").write_text("psutil\n", encoding="utf-8")
        (self.root / "README_PHASE9.md").write_text("phase9\n", encoding="utf-8")
        shutil.copy(ROOT / "config" / "gateway_config.phase9.example.json", self.root / "config" / "gateway_config.phase9.example.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rollback_restores_latest_backup_and_keeps_pre_rollback_backup(self):
        cfg = self.root / "claude_desktop_config.json"
        original = {"mcpServers": {"filesystem": {"command": "node"}, "other": {"command": "ok"}}}
        locked_atomic_write_json(cfg, original)
        migrate_claude_desktop_config(config_path=cfg, python_exe="python", gateway_script=self.root / "guardian_gateway.py", root=self.root, guardian_config=self.gateway_config)
        backups = list_claude_config_backups(cfg)
        self.assertEqual(len(backups), 1)
        result = rollback_claude_desktop_config(config_path=cfg)
        self.assertTrue(result["ok"])
        restored = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(restored, original)
        self.assertTrue(Path(result["pre_rollback_backup"]).exists())

    def test_print_guardian_config_contains_single_server(self):
        result = print_guardian_config(python_exe="python", gateway_script="gw.py", root="R:/root", guardian_config="cfg.json")
        self.assertEqual(list(result["mcpServers"].keys()), ["mcp-guardian"])
        self.assertEqual(result["mcpServers"]["mcp-guardian"]["env"]["MCPGUARDIAN_ENABLE_RULE_MUTATION"], "0")

    def test_write_windows_launchers_creates_operator_scripts(self):
        out = self.root / "scripts"
        result = write_windows_launchers(output_dir=out, root=self.root, python_exe="C:/Python311/python.exe", gateway_config=self.gateway_config)
        self.assertTrue(result["ok"])
        self.assertTrue((out / "start_stdio_gateway.ps1").exists())
        self.assertTrue((out / "start_http_gateway.ps1").exists())
        self.assertIn("BearerToken must be 32+", (out / "start_http_gateway.ps1").read_text(encoding="utf-8"))
        self.assertTrue((out / "launcher_manifest.json").exists())

    def test_release_manifest_requires_core_files(self):
        result = build_release_manifest(self.root)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["missing"])


if __name__ == "__main__":
    unittest.main()
