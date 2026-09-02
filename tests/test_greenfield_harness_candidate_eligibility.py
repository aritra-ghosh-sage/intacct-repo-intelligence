from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from greenfield_harness.candidate_eligibility import (
    EligibilityError,
    build_eligible_candidates,
    default_seeds,
    validate_fixed_seeds,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _checkout(tmp_path: Path, name: str, *, roots: tuple[str, ...] = ("tests",)) -> tuple[Path, str]:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "harness@example.invalid")
    _git(root, "config", "user.name", "Harness")
    _git(root, "remote", "add", "origin", f"git@github.com:intacct/{name}.git")
    for test_root in roots:
        directory = root / test_root
        directory.mkdir(parents=True)
        (directory / "evidence.txt").write_text("test\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "test evidence")
    return root, _git(root, "rev-parse", "HEAD")


def _metadata(repository: str) -> dict[str, object]:
    return {"full_name": repository, "archived": False}


def _seed(repository: str, root: Path, revision: str, roots: list[str] | None = None) -> dict[str, object]:
    value: dict[str, object] = {"repository": repository, "repo_key": repository.rsplit("/", 1)[1], "local_root": str(root), "revision": revision}
    if roots is not None:
        value["test_roots"] = roots
    return value


def test_eligible_candidate_has_revision_bound_metadata_and_roots(tmp_path: Path) -> None:
    root, revision = _checkout(tmp_path, "ia-test-automation")
    row = build_eligible_candidates([_seed("intacct/ia-test-automation", root, revision, ["tests"])], _metadata, retrieved_at="2026-09-02T00:00:00Z")[0]
    assert row["eligibility_status"] == "eligible"
    assert row["test_roots"] == ["tests"]
    assert row["github_metadata_retrieved_at"] == "2026-09-02T00:00:00Z"
    assert len(str(row["github_metadata_sha256"])) == 64


def test_archived_gateway_is_excluded_but_retains_real_local_roots(tmp_path: Path) -> None:
    root, revision = _checkout(tmp_path, "ia-gwdata-sanity", roots=("testdefinitions", "testscripts"))
    row = build_eligible_candidates([_seed("intacct/ia-gwdata-sanity", root, revision)], lambda repository: {"full_name": repository, "archived": True}, retrieved_at="2026-09-02T00:00:00Z")[0]
    assert row["eligibility_status"] == "excluded_archived"
    assert row["reason"] == "github_repository_archived"
    assert row["test_roots"] == ["testdefinitions", "testscripts"]


def test_inaccessible_candidate_is_retained_as_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    row = build_eligible_candidates([_seed("intacct/ia-selenium", root, "a" * 40, ["src"])], lambda _repository: (_ for _ in ()).throw(RuntimeError("HTTP 404")), retrieved_at="2026-09-02T00:00:00Z")[0]
    assert row["eligibility_status"] == "unavailable"
    assert row["reason"] == "github_metadata_unavailable"
    assert len(str(row["github_metadata_sha256"])) == 64


def test_missing_checkout_and_revision_are_invalid(tmp_path: Path) -> None:
    missing = build_eligible_candidates([_seed("intacct/ia-selenium", tmp_path / "missing", "a" * 40, ["src"])], _metadata)[0]
    assert missing["eligibility_status"] == "invalid"
    assert missing["reason"] == "local_checkout_missing"
    root, _revision = _checkout(tmp_path, "ia-selenium", roots=("src",))
    absent = build_eligible_candidates([_seed("intacct/ia-selenium", root, "a" * 40, ["src"])], _metadata)[0]
    assert absent["eligibility_status"] == "invalid"
    assert "Not a valid object name" not in str(absent["reason"])


def test_invalid_or_missing_test_roots_are_invalid(tmp_path: Path) -> None:
    root, revision = _checkout(tmp_path, "ia-restapi-automation-tests", roots=("features",))
    missing = build_eligible_candidates([_seed("intacct/ia-restapi-automation-tests", root, revision, [])], _metadata)[0]
    unsafe = build_eligible_candidates([_seed("intacct/ia-restapi-automation-tests", root, revision, ["../tests"])], _metadata)[0]
    assert missing["reason"] == "missing_test_roots"
    assert unsafe["reason"] == "unsafe_test_root"


def test_gateway_roots_are_derived_from_pinned_checkout(tmp_path: Path) -> None:
    for repository in ("ia-gwdata-gl", "ia-gwdata-sanity"):
        root, revision = _checkout(tmp_path, repository, roots=("testdefinitions", "testscripts"))
        row = build_eligible_candidates([_seed(f"intacct/{repository}", root, revision)], _metadata)[0]
        assert row["eligibility_status"] == "eligible"
        assert row["test_roots"] == ["testdefinitions", "testscripts"]


def test_seed_override_cannot_expand_or_shrink_fixed_boundary() -> None:
    seeds = default_seeds()
    assert validate_fixed_seeds(seeds) == seeds
    with pytest.raises(EligibilityError, match="exactly the fixed repositories"):
        validate_fixed_seeds(seeds[:-1])
    with pytest.raises(EligibilityError, match="exactly the fixed repositories"):
        validate_fixed_seeds([*seeds[:-1], {**seeds[-1], "repository": "intacct/other-tests"}])
