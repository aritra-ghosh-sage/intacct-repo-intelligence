import subprocess
from pathlib import Path

from greenfield.test_index import index_gherkin_tests, retrieve_candidates


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_index_uses_declared_root_and_exact_terms_only(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "features").mkdir(parents=True)
    (root / "other").mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "features" / "budget.feature").write_text("Feature: Budget\nScenario: Enable inception reporting\n", encoding="utf-8")
    (root / "other" / "ignored.feature").write_text("Feature: Budget\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    revision = _git(root, "rev-parse", "HEAD")
    index = index_gherkin_tests(root=root, revision=revision, test_roots=["features"])
    assert [item["path"] for item in index["tests"]] == ["features/budget.feature"]
    assert retrieve_candidates(index, evidence_terms=["inception"])[0]["path"] == "features/budget.feature"
