import asyncio
import sys
import unittest
from pathlib import Path

from guardian.backend_client import AsyncStdioBackendClient, BackendConfig

ROOT = Path(__file__).resolve().parent


class BackendClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_list_and_call_tool(self):
        client = AsyncStdioBackendClient(
            BackendConfig(
                name="fake",
                command=sys.executable,
                args=[str(ROOT / "fake_mcp_backend.py")],
                tool_prefix="fake",
                timeout_seconds=5,
            )
        )
        try:
            await client.initialize()
            tools = await client.list_tools()
            self.assertIn("echo", [tool["name"] for tool in tools])
            self.assertIn("read_file", [tool["name"] for tool in tools])
            result = await client.call_tool("echo", {"message": "hello"})
            self.assertIn("content", result)
            self.assertIn("hello", result["content"][0]["text"])
        finally:
            await client.stop()


if __name__ == "__main__":
    unittest.main()
