"""Small, deterministic index for revision-pinned Gherkin test sources."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

FEATURE = re.compile(r"^\s*Feature:\s*(.+)$", re.MULTILINE)
SCENARIO = re.compile(r"^\s*Scenario(?: Outline)?:\s*(.+)$", re.MULTILINE)


def _show(root: Path, revision: str, path: str) -> str | None:
    result = subprocess.run(["git", "-C", str(root), "show", f"{revision}:{path}"], capture_output=True, text=True, check=False, timeout=30)
    return result.stdout if result.returncode == 0 else None


def index_gherkin_tests(*, root: str | Path, revision: str, test_roots: list[str]) -> dict[str, Any]:
    """Index only declared feature roots; other formats are intentionally absent."""

    repo = Path(root).resolve()
    listing = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", revision], capture_output=True, text=True, check=False, timeout=30)
    if listing.returncode:
        raise ValueError("test index revision is unavailable")
    paths = sorted(path for path in listing.stdout.splitlines() if path.endswith(".feature") and any(path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/") for prefix in test_roots))
    tests: list[dict[str, Any]] = []
    for path in paths:
        content = _show(repo, revision, path)
        if content is None:
            continue
        feature = FEATURE.search(content)
        scenarios = [match.group(1).strip() for match in SCENARIO.finditer(content)]
        tests.append({"path": path, "feature": feature.group(1).strip() if feature else None, "scenarios": scenarios})
    return {"schema_version": "0.1", "analysis_kind": "greenfield_test_index", "repository_revision": revision, "format": "gherkin", "test_roots": sorted(test_roots), "tests": tests, "status": "available"}


def retrieve_candidates(index: dict[str, Any], *, evidence_terms: list[str]) -> list[dict[str, Any]]:
    """Return exact test files only when a confirmed evidence term appears."""

    terms = {term.lower() for term in evidence_terms if isinstance(term, str) and len(term) >= 3}
    if not terms:
        return []
    results = []
    for test in index.get("tests", []):
        haystack = " ".join([test.get("path", ""), test.get("feature") or "", *test.get("scenarios", [])]).lower()
        matched = sorted(term for term in terms if term in haystack)
        if matched:
            results.append({**test, "matched_terms": matched})
    return results


__all__ = ["index_gherkin_tests", "retrieve_candidates"]
