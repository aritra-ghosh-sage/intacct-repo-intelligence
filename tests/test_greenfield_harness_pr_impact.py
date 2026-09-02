from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from greenfield_harness.pr_impact import ImpactHandoff, PrImpactError, run_pr_impact


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _repo(root: Path, name: str) -> tuple[Path, str, str]:
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "user.name", "Harness")
    _git(repo, "remote", "add", "origin", f"git@github.com:example/{name}.git")
    return repo, "", ""


class _Provider:
    def summarize(self, case_summary: dict[str, object]) -> dict[str, object]:
        items = case_summary["extraction"]
        assert isinstance(items, list)
        item = next(row for row in items if row["value"] == "changedService")
        return {"behaviors": [{"id": "behavior:service", "summary": "Service behavior changed.", "evidence_ids": [item["id"]]}]}


class _BrokenProvider:
    def summarize(self, case_summary: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("AWS unavailable")


def _source(root: Path) -> tuple[Path, str, str]:
    repo, _, _ = _repo(root, "source")
    (repo / "src").mkdir()
    (repo / "src" / "Service.php").write_text("class Service {}\nfunction changedService() {}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "Service.php").write_text("class Service {}\nfunction changedService() {}\nfunction newBehavior() {}\n", encoding="utf-8")
    _git(repo, "commit", "-am", "head", "-q")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _candidate(root: Path) -> tuple[Path, str]:
    repo, _, _ = _repo(root, "tests")
    (repo / "tests").mkdir()
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "tests" / "service_test.php").write_text("changedService();\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "test.yml").write_text("name: test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "tests")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_pr_impact_retains_pinned_test_evidence_and_recommendations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    candidates = [{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"]}]
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact"
    paths = run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=candidates, provider=_Provider(), input_paths=[candidates_path])
    analysis = json.loads(paths["analysis"].read_text())
    assert analysis["coverage"][0]["status"] == "strong_candidate"
    assert analysis["coverage"][0]["ci_execution"]["status"] == "unavailable"
    assert json.loads(paths["recommendations"].read_text())["recommendations"] == []
    assert ImpactHandoff.validate(output)["status"] == "complete"


def test_pr_impact_stops_after_retained_ai_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-failed"
    with pytest.raises(PrImpactError, match="retained blocked bundle"):
        run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"]}], provider=_BrokenProvider())
    assert (output / "failure.json").is_file()
    assert ImpactHandoff.validate(output)["status"] == "blocked"


def test_pr_impact_recommends_a_test_only_for_a_retained_coverage_gap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    (candidate / "tests" / "service_test.php").write_text("unrelated();\n", encoding="utf-8")
    _git(candidate, "commit", "-am", "remove matching evidence", "-q")
    revision = _git(candidate, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-gap"
    paths = run_pr_impact(
        source_root=source,
        output_dir=output,
        pr=1,
        base_revision=base,
        target_revision=head,
        candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"]}],
        provider=_Provider(),
    )
    assessment = json.loads(paths["assessment"].read_text())
    recommendations = json.loads(paths["recommendations"].read_text())
    assert assessment["coverage"][0]["status"] == "no_evidence"
    assert recommendations["recommendations"][0]["source_evidence_ids"]
    assert recommendations["recommendations"][0]["reason"] == "no_matching_pinned_test_evidence"
