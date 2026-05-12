import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from guardian.packaging import cli


class MCPGuardianCtlTests(unittest.TestCase):
    def _run(self, argv):
        buf = StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, json.loads(buf.getvalue())

    def test_make_token_generates_long_token(self):
        code, result = self._run(["make-token", "--bytes", "32"])
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(result["token"]), 32)

    def test_write_launchers_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = root / "gateway_config.json"
            cfg.write_text("{}", encoding="utf-8")
            out = root / "scripts"
            code, result = self._run(["write-launchers", "--output-dir", str(out), "--root", str(root), "--python-exe", "python", "--gateway-config", str(cfg)])
            self.assertEqual(code, 0)
            self.assertTrue(result["ok"])
            self.assertTrue((out / "run_diagnostics.ps1").exists())


if __name__ == "__main__":
    unittest.main()
