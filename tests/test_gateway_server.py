import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.gateway_protocol import parse_message

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent


class GatewayServerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        locked_atomic_write_json(
            self.root / "config" / "gateway_config.json",
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

    def tearDown(self):
        self.tmp.cleanup()

    def test_gateway_stdio_smoke(self):
        import os
        import subprocess

        env = os.environ.copy()
        env["MCPGUARDIAN_ROOT"] = str(self.root)
        env["MCPGUARDIAN_CONFIG"] = str(self.root / "config" / "gateway_config.json")
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "guardian_gateway.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            def send(obj):
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                proc.stdin.flush()
                assert proc.stdout is not None
                return json.loads(proc.stdout.readline())

            init = send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(init["result"]["serverInfo"]["name"], "mcpguardian-gateway")
            # initialized is a notification: no direct response.
            assert proc.stdin is not None
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()
            tools = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            names = [tool["name"] for tool in tools["result"]["tools"]]
            self.assertIn("fake__echo", names)
            self.assertIn("fake__read_file", names)
            self.assertIn("mcpguardian_gateway_status", names)
            call = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "fake__echo", "arguments": {"message": "hello"}}})
            self.assertIn("hello", call["result"]["content"][0]["text"])
            send({"jsonrpc": "2.0", "id": 4, "method": "shutdown", "params": {}})
        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
