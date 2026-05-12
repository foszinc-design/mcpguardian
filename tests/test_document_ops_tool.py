import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from guardian.atomic_io import locked_atomic_write_json
from guardian.gateway_router import GatewayConfig, GatewayRouter

ROOT = Path(__file__).resolve().parents[1]


def payload(result):
    return json.loads(result["content"][0]["text"])


class DocumentOpsToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "runs").mkdir()
        shutil.copy(ROOT / "config" / "active_rules.json", self.root / "config" / "active_rules.json")
        locked_atomic_write_json(self.root / "config" / "pending_rules.json", {"rules": []})
        (self.root / "config" / "rule_history.jsonl").write_text("", encoding="utf-8")
        self.config_path = self.root / "config" / "gateway_config.json"
        locked_atomic_write_json(self.config_path, {"paths": {"runs_dir": "runs", "active_rules": "config/active_rules.json", "pending_rules": "config/pending_rules.json", "rule_history": "config/rule_history.jsonl"}, "allowed_roots": ["."], "backends": {}})

    def tearDown(self):
        self.tmp.cleanup()

    async def _router(self):
        router = GatewayRouter(GatewayConfig.load(root=self.root, config_path=self.config_path))
        self.addAsyncCleanup(router.shutdown)
        return router

    async def test_docx_text_extraction(self):
        docx = self.root / "sample.docx"
        _write_minimal_docx(docx, "안녕하세요", "MCPGuardian 123")
        router = await self._router()
        result = payload(await router.call_tool("guardian_read_docx", {"path": str(docx)}))
        self.assertTrue(result["ok"], result)
        self.assertIn("안녕하세요", result["data"]["text"])
        self.assertIn("MCPGuardian 123", result["data"]["text"])

    async def test_pdf_inspection(self):
        pdf = self.root / "sample.pdf"
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n2 0 obj << /Type /Page /Parent 3 0 R >> endobj\n4 0 obj << /Title (Report) /Author (Me) >> endobj\nBT (hello pdf) Tj ET\n%%EOF")
        router = await self._router()
        result = payload(await router.call_tool("guardian_inspect_pdf", {"path": str(pdf)}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["metadata"]["title"], "Report")
        self.assertIn("hello pdf", result["data"]["text_hints"])

    async def test_xlsx_inspection(self):
        try:
            from openpyxl import Workbook
        except Exception:
            self.skipTest("openpyxl unavailable")
        xlsx = self.root / "sample.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append(["name", "value"])
        ws.append(["a", 1])
        wb.save(xlsx)
        router = await self._router()
        result = payload(await router.call_tool("guardian_inspect_xlsx", {"path": str(xlsx)}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["sheet_count"], 1)
        self.assertEqual(result["data"]["sheets"][0]["name"], "Data")


def _write_minimal_docx(path: Path, *paragraphs: str) -> None:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'''
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


if __name__ == "__main__":
    unittest.main()
