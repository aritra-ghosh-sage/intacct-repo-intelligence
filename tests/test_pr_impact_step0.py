from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import validate_pr_impact_step0 as validator


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "examples/pr-impact"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _repo(tmp_path: Path, *, rename: bool = False) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app").mkdir()
    (repo / "app/a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "app/deleted.txt").write_text("gone\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    if rename:
        (repo / "app/a.txt").rename(repo / "app/b.txt")
    else:
        (repo / "app/a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        (repo / "app/added.txt").write_text("new\n", encoding="utf-8")
        (repo / "app/deleted.txt").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "target")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _fixture(tmp_path: Path, repo: Path, base: str, target: str, files: list[dict[str, str]], **extra: object) -> Path:
    document: dict[str, object] = {
        "schema_version": "0.1",
        "analysis_kind": "pr_impact_step_0",
        "pull_request": {"repository": "intacct/ia-app", "number": 1, "url": "https://github.com/intacct/ia-app/pull/1", "base_revision": base, "target_revision": target},
        "changed_files": files,
        "changed_items": [{"type": "business_logic", "status": "assessed"}],
        "affected_surfaces": {"entities": [], "api": [], "ui": {}, "database": {}, "permissions": {}},
        "related_repositories": [{"repository": "intacct/example", "relationship": "candidate", "status": "coverage_unknown"}],
        "test_obligations": {"existing_or_expected": [], "recommended": [], "unresolved": []},
        "review_evidence": {"automated": [], "human": []},
        "assessment": {"confidence": "medium", "risk_level": "low", "blockers": [], "unresolved": []},
        "provenance": {"source_snapshot": "target_revision", "review_snapshot_date": "2026-08-08", "generated_from": ["Git diff"]},
    }
    document.update(extra)
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_golden_fixtures_parse_and_validate() -> None:
    repo = Path("/Users/aritra.ghosh/projects/main")
    for name in ("ia-app-pr-49156.yaml", "ia-app-pr-48706.yaml"):
        report = validator.validate_fixture(FIXTURES / name, repo)
        assert report["status"] == "pass", report


def test_required_sections_and_status_are_enforced(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path)
    path = _fixture(tmp_path, repo, base, target, [{"path": "app/a.txt", "status": "bogus"}])
    document = yaml.safe_load(path.read_text())
    del document["pull_request"]
    path.write_text(yaml.safe_dump(document))
    report = validator.validate_fixture(path, repo)
    assert any(item["code"] == "missing_required_section" for item in report["errors"])


@pytest.mark.parametrize("section", [
    "changed_items", "affected_surfaces", "related_repositories", "test_obligations",
    "review_evidence", "assessment", "provenance",
])
def test_documented_required_section_cannot_be_omitted(tmp_path: Path, section: str) -> None:
    repo, base, target = _repo(tmp_path)
    path = _fixture(
        tmp_path, repo, base, target, [{"path": "app/a.txt", "status": "modified"}],
        changed_items=[{"type": "business_logic"}],
        affected_surfaces={"entities": [], "api": [], "ui": {}, "database": {}, "permissions": {}},
        related_repositories=[{"repository": "intacct/example", "relationship": "candidate"}],
        test_obligations={"existing_or_expected": [], "recommended": [], "unresolved": []},
        review_evidence={"automated": [], "human": []},
        assessment={"confidence": "medium", "risk_level": "low", "blockers": [], "unresolved": []},
        provenance={"source_snapshot": "target_revision", "review_snapshot_date": "2026-08-08", "generated_from": ["Git diff"]},
    )
    document = yaml.safe_load(path.read_text())
    del document[section]
    path.write_text(yaml.safe_dump(document))
    report = validator.validate_fixture(path, repo)
    assert any(item["code"] == "missing_required_section" for item in report["errors"])


@pytest.mark.parametrize("field", ["base_revision", "target_revision"])
def test_missing_revision_fails(tmp_path: Path, field: str) -> None:
    repo, base, target = _repo(tmp_path)
    path = _fixture(tmp_path, repo, base, target, [{"path": "app/a.txt", "status": "modified"}])
    document = yaml.safe_load(path.read_text())
    del document["pull_request"][field]
    path.write_text(yaml.safe_dump(document))
    report = validator.validate_fixture(path, repo)
    assert any(item["code"] == "missing_required_field" for item in report["errors"])


def test_non_full_or_ambiguous_revision_fails(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path)
    path = _fixture(tmp_path, repo, base, target, [{"path": "app/a.txt", "status": "modified"}])
    document = yaml.safe_load(path.read_text())
    document["pull_request"]["base_revision"] = base[:8]
    path.write_text(yaml.safe_dump(document))
    report = validator.validate_fixture(path, repo)
    assert any(item["code"] == "invalid_git_revision" for item in report["errors"])


def test_added_modified_deleted_validate(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path)
    files = [
        {"path": "app/a.txt", "status": "modified"},
        {"path": "app/added.txt", "status": "added"},
        {"path": "app/deleted.txt", "status": "deleted"},
    ]
    report = validator.validate_fixture(_fixture(tmp_path, repo, base, target, files), repo)
    assert report["status"] == "pass", report


def test_path_and_status_mismatch_duplicate_and_unsafe_paths_fail(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path)
    files = [
        {"path": "app/a.txt", "status": "added"},
        {"path": "app/a.txt", "status": "modified"},
        {"path": "../escape", "status": "deleted"},
    ]
    report = validator.validate_fixture(_fixture(tmp_path, repo, base, target, files), repo)
    codes = {item["code"] for item in report["errors"]}
    assert {"changed_files_mismatch", "duplicate_path", "unsafe_path"} <= codes


def test_missing_evidence_and_invalid_line_fail(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path)
    files = [{"path": "app/a.txt", "status": "modified"}, {"path": "app/added.txt", "status": "added"}, {"path": "app/deleted.txt", "status": "deleted"}]
    document = {"changed_items": [{"evidence": [{"source": "app/a.txt", "line": 99}, "app/nope.txt"]}]}
    report = validator.validate_fixture(_fixture(tmp_path, repo, base, target, files, **document), repo)
    codes = {item["code"] for item in report["errors"]}
    assert {"missing_evidence_path", "invalid_evidence_line"} <= codes


def test_rename_is_explicitly_unsupported(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path, rename=True)
    path = _fixture(tmp_path, repo, base, target, [{"path": "app/b.txt", "status": "added"}, {"path": "app/a.txt", "status": "deleted"}])
    report = validator.validate_fixture(path, repo)
    assert any(item["code"] == "unsupported_change_type" for item in report["errors"])


def test_malformed_git_diff_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base, target = _repo(tmp_path)
    path = _fixture(tmp_path, repo, base, target, [{"path": "app/a.txt", "status": "modified"}])
    monkeypatch.setattr(validator, "collect_changed_paths", lambda *args: (_ for _ in ()).throw(validator.DeltaUnavailable("malformed raw Git diff")))
    report = validator.validate_fixture(path, repo)
    assert any(item["code"] == "git_diff_error" for item in report["errors"])


def test_unresolved_and_missing_review_reference_are_warnings(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path)
    files = [{"path": "app/a.txt", "status": "modified"}, {"path": "app/added.txt", "status": "added"}, {"path": "app/deleted.txt", "status": "deleted"}]
    path = _fixture(tmp_path, repo, base, target, files, assessment={"confidence": "medium", "risk_level": "low", "blockers": [], "unresolved": ["not checked"]}, review_evidence={"automated": [{"type": "pull_request_review", "reviewed_revision": base}], "human": []})
    report = validator.validate_fixture(path, repo)
    assert report["status"] == "pass"
    assert any(item["code"] == "stale_review_evidence" for item in report["warnings"])


def test_json_envelope_and_cli_exit_codes(tmp_path: Path) -> None:
    repo, base, target = _repo(tmp_path)
    files = [{"path": "app/a.txt", "status": "modified"}, {"path": "app/added.txt", "status": "added"}, {"path": "app/deleted.txt", "status": "deleted"}]
    path = _fixture(tmp_path, repo, base, target, files, assessment={"confidence": "medium", "risk_level": "low", "blockers": [], "unresolved": ["x"]})
    command = ["./.venv/bin/python", "scripts/validate_pr_impact_step0.py", "--fixture", str(path), "--repo-root", str(repo), "--json"]
    ok = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert ok.returncode == 0
    payload = json.loads(ok.stdout)
    assert {"schema_version", "fixture", "repository", "status", "errors", "warnings", "checks"} <= payload.keys()
    bad = yaml.safe_load(path.read_text())
    bad["changed_files"][0]["status"] = "added"
    path.write_text(yaml.safe_dump(bad))
    failed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert failed.returncode == 1
