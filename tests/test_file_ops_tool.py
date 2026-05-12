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


class NativeFileOpsToolTests(unittest.IsolatedAsyncioTestCase):
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
                "paths": {"runs_dir": "runs", "active_rules": "config/active_rules.json", "pending_rules": "config/pending_rules.json", "rule_history": "config/rule_history.jsonl"},
                "allowed_roots": ["."],
                "gateway": {"tool_separator": "__"},
                "backends": {},
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    async def _router(self):
        router = GatewayRouter(GatewayConfig.load(root=self.root, config_path=self.config_path))
        self.addAsyncCleanup(router.shutdown)
        return router

    async def test_write_read_edit_and_list_file(self):
        router = await self._router()
        path = self.root / "work" / "hello.txt"
        write = payload(await router.call_tool("guardian_write_file", {"path": str(path), "content": "hello\nworld\n"}))
        self.assertTrue(write["ok"])
        read = payload(await router.call_tool("guardian_read_file", {"path": str(path)}))
        self.assertEqual(read["data"]["text"], "hello\nworld\n")
        edit = payload(await router.call_tool("guardian_edit_file", {"path": str(path), "old_text": "world", "new_text": "MCPGuardian"}))
        self.assertTrue(edit["ok"])
        listed = payload(await router.call_tool("guardian_list_directory", {"path": str(path.parent)}))
        self.assertIn("hello.txt", [item["name"] for item in listed["data"]["entries"]])
        trace_files = list((self.root / "runs").glob("*/trace.jsonl"))
        self.assertGreaterEqual(len(trace_files), 4)

    async def test_path_policy_blocks_outside_file(self):
        router = await self._router()
        outside = Path(tempfile.gettempdir()) / "mcpguardian_native_outside.txt"
        result = await router.call_tool("guardian_read_file", {"path": str(outside)})
        self.assertTrue(result.get("isError"))
        self.assertIn("PATH_POLICY_DENIED", result["content"][0]["text"])

    async def test_directory_tree_and_search(self):
        router = await self._router()
        target = self.root / "src" / "a.txt"
        payload(await router.call_tool("guardian_write_file", {"path": str(target), "content": "needle"}))
        tree = payload(await router.call_tool("guardian_directory_tree", {"path": str(self.root / "src"), "max_depth": 2}))
        self.assertTrue(tree["ok"])
        search = payload(await router.call_tool("guardian_search_files", {"path": str(self.root), "pattern": "*.txt", "content_query": "needle"}))
        self.assertEqual(len(search["data"]["results"]), 1)

    async def test_policy_blocks_native_write_before_execution(self):
        locked_atomic_write_json(
            self.root / "config" / "active_rules.json",
            {"rules": [{"id": "native.block.write.v1", "status": "active", "scope": "gateway", "task_types": ["mcp_tool_call"], "severity": "critical", "enforcement": "block", "condition": {"mcp_backend": "native", "mcp_tool_name": "guardian_write_file"}, "required_artifacts": [], "message": "native write blocked"}]},
        )
        router = await self._router()
        path = self.root / "blocked.txt"
        result = await router.call_tool("guardian_write_file", {"path": str(path), "content": "no"})
        self.assertTrue(result.get("isError"))
        self.assertFalse(path.exists())
        self.assertIn("POLICY_BLOCKED", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
