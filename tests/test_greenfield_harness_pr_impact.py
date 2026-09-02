from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import greenfield_harness.pr_impact as impact
from greenfield_harness.pr_impact import ImpactHandoff, PrImpactError, run_pr_impact
from greenfield_harness.pr_impact_planner import PlannerError, initial_plan


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
    def initial_plan(self, request: dict[str, object]) -> dict[str, object]:
        items = request["extraction"]
        assert isinstance(items, list)
        item = next(row for row in items if row["value"] == "changedService")
        return {"behaviors": [{"id": "behavior:service", "summary": "Service behavior changed.", "evidence_ids": [item["id"]]}], "questions": [{"id": "source:service", "type": "source_flow", "question": "Find service flow.", "evidence_ids": [item["id"]], "source_terms": ["changedService"]}]}

    def replan(self, request: dict[str, object]) -> dict[str, object]:
        items = request["extraction"]
        assert isinstance(items, list)
        item = next(row for row in items if row["value"] == "changedService")
        return {"questions": [{"id": "test:service", "type": "test_discovery", "question": "Find service tests.", "evidence_ids": [item["id"]], "source_terms": ["changedService"], "ai_terms": ["service behavior"]}]}


class _BrokenProvider:
    def initial_plan(self, request: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("AWS unavailable")

    def replan(self, request: dict[str, object]) -> dict[str, object]:
        raise AssertionError("replan must not run")


class _AiOnlyProvider(_Provider):
    def replan(self, request: dict[str, object]) -> dict[str, object]:
        items = request["extraction"]
        assert isinstance(items, list)
        item = next(row for row in items if row["value"] == "changedService")
        return {"questions": [{"id": "test:service", "type": "test_discovery", "question": "Find semantic service tests.", "evidence_ids": [item["id"]], "source_terms": ["changedService"], "ai_terms": ["service behavior"]}]}


class _InvalidPlanner(_Provider):
    def initial_plan(self, request: dict[str, object]) -> dict[str, object]:
        value = super().initial_plan(request)
        value["questions"][0]["source_terms"] = ["invented"]
        return value


class _ReplanBrokenProvider(_Provider):
    def replan(self, request: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("replan unavailable")


class _UncoveredBehaviorProvider(_Provider):
    def initial_plan(self, request: dict[str, object]) -> dict[str, object]:
        items = request["extraction"]
        assert isinstance(items, list)
        service = next(row for row in items if row["value"] == "changedService")
        new = next(row for row in items if row["value"] == "newBehavior")
        return {"behaviors": [{"id": "behavior:service", "summary": "Service behavior changed.", "evidence_ids": [service["id"]]}, {"id": "behavior:new", "summary": "New behavior changed.", "evidence_ids": [new["id"]]}], "questions": [{"id": "source:both", "type": "source_flow", "question": "Find flows.", "evidence_ids": [service["id"], new["id"]], "source_terms": ["changedService", "newBehavior"]}]}

    def replan(self, request: dict[str, object]) -> dict[str, object]:
        items = request["extraction"]
        assert isinstance(items, list)
        service = next(row for row in items if row["value"] == "changedService")
        return {"questions": [{"id": "test:service", "type": "test_discovery", "question": "Find service tests.", "evidence_ids": [service["id"]], "source_terms": ["changedService"], "ai_terms": []}]}


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
    candidates = [{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"}]
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact"
    paths = run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=candidates, provider=_Provider(), input_paths=[candidates_path])
    analysis = json.loads(paths["analysis"].read_text())
    assert analysis["coverage"][0]["status"] == "strong_candidate"
    assert analysis["coverage"][0]["ci_execution"]["status"] == "unavailable"
    assert json.loads(paths["recommendations"].read_text())["recommendations"] == []
    assert (output / "planning-report.json").is_file()
    assert (output / "tool-ledger.json").is_file()
    assert ImpactHandoff.validate(output)["status"] == "complete"


def test_pr_impact_stops_after_retained_ai_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-failed"
    with pytest.raises(PrImpactError, match="retained blocked bundle"):
        run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"}], provider=_BrokenProvider())
    assert (output / "planner-failure.json").is_file()
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
        candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"}],
        provider=_Provider(),
    )
    assessment = json.loads(paths["assessment"].read_text())
    recommendations = json.loads(paths["recommendations"].read_text())
    assert assessment["coverage"][0]["status"] == "no_evidence"
    assert recommendations["recommendations"][0]["source_evidence_ids"]
    assert recommendations["recommendations"][0]["reason"] == "no_matching_pinned_test_evidence"


def test_pr_impact_keeps_ai_expanded_test_match_as_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, _revision = _candidate(tmp_path)
    (candidate / "tests" / "service_test.php").write_text("service behavior\n", encoding="utf-8")
    _git(candidate, "commit", "-am", "semantic-only match", "-q")
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-ai-only"
    paths = run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": _git(candidate, "rev-parse", "HEAD"), "test_roots": ["tests"], "eligibility_status": "eligible"}], provider=_AiOnlyProvider())
    assert json.loads(paths["assessment"].read_text())["coverage"][0]["status"] == "candidate"
    assert json.loads(paths["recommendations"].read_text())["recommendations"] == []


def test_pr_impact_blocks_invalid_planner_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-invalid"
    with pytest.raises(PrImpactError, match="planner initial turn failed"):
        run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"}], provider=_InvalidPlanner())
    assert ImpactHandoff.validate(output)["status"] == "blocked"


def test_pr_impact_blocks_replan_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-replan-failed"
    with pytest.raises(PrImpactError, match="planner replan turn failed"):
        run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"}], provider=_ReplanBrokenProvider())
    assert ImpactHandoff.validate(output)["status"] == "blocked"


def test_pr_impact_retains_source_search_unavailable_gap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(impact, "_grep_at", lambda *_args, **_kwargs: (_ for _ in ()).throw(PrImpactError("search unavailable")))
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-source-gap"
    paths = run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"}], provider=_Provider())
    assert json.loads(paths["ledger"].read_text())["gaps"][0]["status"] == "unavailable"


def test_source_investigation_caps_each_question_without_starving_later_questions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, _base, head = _source(tmp_path)
    context = {"source": {"local_root": str(source), "target_revision": head}}
    extraction = {"extraction_sha256": "a" * 64}
    plan = {
        "questions": [
            {
                "id": f"source:{index}",
                "evidence_ids": ["extract:service"],
                "source_terms": ["changedService"],
            }
            for index in range(3)
        ]
    }
    limits: list[int] = []

    def grep(*_args: object, **kwargs: object) -> tuple[list[tuple[str, int, str]], bool]:
        limits.append(int(kwargs["limit"]))
        return [("src/Service.php", line, "function changedService() {}") for line in range(1, 5)], False

    monkeypatch.setattr(impact, "_grep_at", grep)
    ledger = impact.inspect_source_questions(context, extraction, plan)
    assert limits == [4, 4, 4]
    assert [row["question_id"] for row in ledger["evidence"]].count("source:2") == 4


def test_pr_impact_blocks_replan_that_omits_a_behavior(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-uncovered"
    with pytest.raises(PrImpactError, match="planner replan turn failed"):
        run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"}], provider=_UncoveredBehaviorProvider())


def test_pr_impact_caps_candidate_matches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, _revision = _candidate(tmp_path)
    (candidate / "tests" / "service_test.php").write_text("changedService\n" * 25, encoding="utf-8")
    _git(candidate, "commit", "-am", "many matches", "-q")
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-capped"
    paths = run_pr_impact(source_root=source, output_dir=output, pr=1, base_revision=base, target_revision=head, candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": _git(candidate, "rev-parse", "HEAD"), "test_roots": ["tests"], "eligibility_status": "eligible"}], provider=_Provider())
    evidence = json.loads(paths["test_evidence"].read_text())
    assert len(evidence["evidence"]) == 20
    assert evidence["gaps"][0]["reason"] == "candidate_match_budget_exhausted"


def test_pr_impact_searches_only_eligible_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-eligible-only"
    candidates_path = tmp_path / "eligible-candidates.json"
    candidates = [
        {"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"], "eligibility_status": "eligible"},
        {"repository": "example/excluded", "local_root": str(tmp_path / "missing"), "revision": "a" * 40, "test_roots": [], "eligibility_status": "excluded_archived"},
    ]
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    paths = run_pr_impact(
        source_root=source,
        output_dir=output,
        pr=1,
        base_revision=base,
        target_revision=head,
        candidates=candidates,
        provider=_Provider(),
        input_paths=[candidates_path],
    )
    context = json.loads(paths["context"].read_text())
    assert [row["repository"] for row in context["candidate_repositories"]] == ["example/tests"]
    assert context["input_provenance"][0]["path"] == str(candidates_path.resolve())
    assert len(context["input_provenance"][0]["sha256"]) == 64


def test_pr_impact_rejects_candidate_without_eligibility_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, base, head = _source(tmp_path)
    candidate, revision = _candidate(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PrImpactError, match="eligibility_status must be explicit"):
        run_pr_impact(
            source_root=source,
            output_dir=tmp_path / "artifacts" / "greenfield-harness" / "pr-impact-unvalidated",
            pr=1,
            base_revision=base,
            target_revision=head,
            candidates=[{"repository": "example/tests", "local_root": str(candidate), "revision": revision, "test_roots": ["tests"]}],
            provider=_Provider(),
        )


def test_initial_plan_caps_behavior_count() -> None:
    extraction = {"items": [{"id": "extract:one", "value": "one"}]}
    plan = {"behaviors": [{"id": f"behavior:{index}", "summary": "Changed.", "evidence_ids": ["extract:one"]} for index in range(5)], "questions": [{"id": "source:one", "type": "source_flow", "question": "Find one.", "evidence_ids": ["extract:one"], "source_terms": ["one"]}]}
    with pytest.raises(PlannerError, match="initial plan requires"):
        initial_plan(plan, extraction)
