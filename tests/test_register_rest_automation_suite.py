from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.register_rest_automation_suite import validate_suite


class RegisterRestAutomationSuiteTests(unittest.TestCase):
    def test_accepts_only_an_explicit_mapping_inside_the_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping = root / "object-mapping.json"
            mapping.write_text("{}", encoding="utf-8")
            self.assertEqual(validate_suite(root, mapping), "object-mapping.json")


if __name__ == "__main__":
    unittest.main()
