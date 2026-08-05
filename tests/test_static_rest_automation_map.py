from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from catalog.rest_automation_contract import (
    RestAutomationContractError,
    load_static_map,
)


def _entry(method: str) -> dict[str, object]:
    return {
        "entity": None,
        "method": method,
        "path_spec": "openapi/account.yaml",
        "ref_chain": [],
        "registry": {
            "kind": "object",
            "module": "accounts",
            "path": "account",
            "release": "v1",
        },
        "revision": "v1",
        "route": "/objects/accounts/account",
        "target_repo": "ia-main",
        "token": "account",
    }


class StaticRestAutomationMapTests(unittest.TestCase):
    def test_empty_map_is_not_a_runnable_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.json"
            path.write_text('{"entries":[],"static_map_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(RestAutomationContractError, "contain reviewed evidence"):
                load_static_map(path)

    def test_method_distinguishes_crud_entries_for_one_token_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.json"
            path.write_text(json.dumps({"entries": [_entry("GET"), _entry("POST")], "static_map_version": 1}, separators=(",", ":")), encoding="utf-8")
            entries = load_static_map(path)
        self.assertEqual(["GET", "POST"], [entry.method for entry in entries])

