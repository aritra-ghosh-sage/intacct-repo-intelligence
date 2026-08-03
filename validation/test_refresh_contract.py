from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from catalog.refresh_contract import runtime_fingerprint


class RefreshContractTests(unittest.TestCase):
    def test_fingerprint_tracks_runtime_inputs_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog").mkdir()
            (root / "scripts").mkdir()
            (root / "docs").mkdir()
            (root / "catalog/schema.sql").write_text("CREATE TABLE x(id);\n")
            (root / "catalog/runtime.py").write_text("VALUE = 1\n")
            (root / "scripts/query_only.py").write_text("VALUE = 1\n")
            (root / "docs/readme.md").write_text("one\n")
            first = runtime_fingerprint(root)
            self.assertEqual(first, runtime_fingerprint(root))
            (root / "docs/readme.md").write_text("two\n")
            (root / "scripts/query_only.py").write_text("VALUE = 2\n")
            self.assertEqual(first, runtime_fingerprint(root))
            (root / "catalog/runtime.py").write_text("VALUE = 2\n")
            self.assertNotEqual(first, runtime_fingerprint(root))


if __name__ == "__main__":
    unittest.main()
