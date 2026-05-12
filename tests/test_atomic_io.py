from pathlib import Path
import json
import tempfile
import unittest

from guardian.atomic_io import append_jsonl, locked_atomic_write_json, load_json


class AtomicIOTests(unittest.TestCase):
    def test_locked_atomic_write_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            locked_atomic_write_json(path, {"ok": True, "items": [1, 2]})
            self.assertEqual(load_json(path), {"ok": True, "items": [1, 2]})

    def test_append_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            append_jsonl(path, {"event": "a"})
            append_jsonl(path, {"event": "b"})
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([line["event"] for line in lines], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
