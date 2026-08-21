"""Audit Greenfield Step 2 source anchors and likely-test evidence read-only."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def audit(report: dict[str, Any]) -> dict[str, Any]:
    changed_paths = {
        item.get("path") if isinstance(item, dict) else item
        for item in report.get("input", {}).get("changed_paths", [])
        if isinstance(item, (dict, str))
    }
    changed_paths.discard(None)
    anchors_by_key: dict[str, dict[str, Any]] = {}
    mappings: list[tuple[str, str, str]] = []
    tests_without_evidence: list[str] = []
    for candidate in report.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        for anchor in candidate.get("source_anchors", []):
            if isinstance(anchor, dict):
                anchor_key = json.dumps(anchor, sort_keys=True)
                anchors_by_key[anchor_key] = anchor
                for mapping in anchor.get("interfaces", []):
                    if isinstance(mapping, dict):
                        mappings.append(
                            (
                                str(anchor.get("source_path")),
                                str(anchor.get("source_symbol", "")),
                                str(mapping.get("interface_id")),
                            )
                        )
        for likely_test in candidate.get("likely_tests", []):
            if isinstance(likely_test, dict) and not likely_test.get("evidence"):
                tests_without_evidence.append(str(likely_test.get("path", "")))

    anchors = list(anchors_by_key.values())
    mapping_counts = Counter(mappings)
    mappings = sorted(mapping_counts)
    anchored_paths = {str(anchor.get("source_path")) for anchor in anchors}
    duplicate_mappings = sorted(
        {key for key, count in mapping_counts.items() if count > 1}
    )
    missing_anchor_paths = sorted(set(changed_paths) - anchored_paths)
    anchorless_sources = sorted(
        str(anchor.get("source_path"))
        for anchor in anchors
        if not anchor.get("interfaces")
    )
    unique_test_gaps = sorted(set(tests_without_evidence))
    return {
        "status": "ok"
        if not (missing_anchor_paths or anchorless_sources or duplicate_mappings or unique_test_gaps)
        else "issues_found",
        "changed_paths_without_source_anchors": missing_anchor_paths,
        "source_anchors_without_interfaces": anchorless_sources,
        "duplicate_source_interface_mappings": [list(item) for item in duplicate_mappings],
        "likely_tests_without_evidence": unique_test_gaps,
        "anchor_count": len(anchors),
        "mapping_count": len(mappings),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise TypeError("report must be an object")
        print(json.dumps(audit(report), indent=2, sort_keys=True))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"greenfield source contract audit failed: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
