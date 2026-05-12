import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from guardian.atomic_io import locked_atomic_write_json
from guardian.guardian_http_gateway import create_http_app

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent


class FakeHttpBackendClient:
    def __init__(self, config):
        self.config = config
        self.calls = []
        self.initialized = False

    async def initialize(self):
        self.initialized = True
        return {"ok": True}

    async def list_tools(self):
        self.initialized = True
        return [
            {"name": "echo", "description": "Echo arguments", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}}},
            {"name": "read_file", "description": "Read a file path", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        ]

    async def call_tool(self, name, arguments=None):
        self.calls.append({"name": name, "arguments": arguments or {}})
        return {"content": [{"type": "text", "text": json.dumps({"name": name, "arguments": arguments or {}}, ensure_ascii=False)}]}

    def health(self):
        return {"name": self.config.name, "running": True, "initialized": self.initialized, "tool_count": 2, "circuit_breaker": {}, "metrics": {}, "stderr_tail": []}

    async def stop(self):
        return None


class HttpGatewayTests(unittest.TestCase):
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
                "http": {"host": "127.0.0.1", "port": 8000, "path": "/mcp", "require_bearer_token": True, "health_endpoint": True},
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

    def _client(self):
        patcher = patch("guardian.gateway_router.AsyncStdioBackendClient", FakeHttpBackendClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = create_http_app(root=self.root, config_path=self.config_path, bearer_token="secret")
        return TestClient(app)

    def _post(self, client, obj, token="secret"):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return client.post("/mcp", content=json.dumps(obj), headers=headers)

    def test_health_without_auth(self):
        with self._client() as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.assertEqual(response.json()["backends"], 1)

    def test_invalid_token_returns_401(self):
        with self._client() as client:
            response = self._post(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token="wrong")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], "UNAUTHORIZED")

    def test_missing_token_returns_401(self):
        with self._client() as client:
            response = self._post(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token=None)
        self.assertEqual(response.status_code, 401)

    def test_http_tool_list_returns_backend_tools(self):
        with self._client() as client:
            init = self._post(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(init.status_code, 200)
            self.assertEqual(init.json()["result"]["serverInfo"]["name"], "mcpguardian-gateway")
            initialized = self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertEqual(initialized.status_code, 202)
            tools = self._post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            self.assertEqual(tools.status_code, 200)
            names = [tool["name"] for tool in tools.json()["result"]["tools"]]
            self.assertIn("fake__echo", names)
            self.assertIn("fake__read_file", names)
            self.assertIn("mcpguardian_gateway_status", names)

    def test_http_tool_call_routes_through_gateway(self):
        with self._client() as client:
            self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            call = self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "fake__echo", "arguments": {"message": "hello-http"}},
                },
            )
            self.assertEqual(call.status_code, 200)
            self.assertIn("hello-http", call.json()["result"]["content"][0]["text"])

    def test_policy_block_is_tool_response_not_401(self):
        locked_atomic_write_json(
            self.root / "config" / "active_rules.json",
            {
                "rules": [
                    {
                        "id": "gateway.block.fake.echo.v1",
                        "status": "active",
                        "scope": "gateway",
                        "task_types": ["mcp_tool_call"],
                        "severity": "critical",
                        "enforcement": "block",
                        "condition": {"mcp_backend": "fake", "mcp_tool_name_any": ["echo"]},
                        "required_artifacts": [],
                        "message": "fake echo blocked for test",
                    }
                ]
            },
        )
        with self._client() as client:
            call = self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "fake__echo", "arguments": {"message": "blocked"}},
                },
            )
            self.assertEqual(call.status_code, 200)
            result = call.json()["result"]
            self.assertTrue(result["isError"])
            self.assertIn("POLICY_BLOCKED", result["content"][0]["text"])

    def test_create_http_app_requires_env_token_when_not_injected(self):
        with patch.dict("os.environ", {"MCPGUARDIAN_BEARER_TOKEN": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                create_http_app(root=self.root, config_path=self.config_path)


if __name__ == "__main__":
    unittest.main()
