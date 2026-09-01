from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from greenfield.analysis_report import (
    AnalysisReportError,
    build_analysis_report,
    canonical_analysis_projection,
    validate_analysis_report,
    validate_projection_consistency,
)
from greenfield.artifact_io import artifact_sha256
from greenfield.publish import (
    _publication_unsigned,
    build_publication,
    publish_github,
    validate_publication,
)
from greenfield.repository_handbook import (
    build_repository_handbook,
    resynchronize_repository_handbook,
    validate_repository_handbook,
)
from greenfield.run_context import build_run_context, validate_run_context
from greenfield.step1_capture import build_report
from greenfield.step8_create import ValidatedDraftAuthorizer
from greenfield.strands_agent import run_strands_analysis
from greenfield.strands_tools import GreenfieldToolbox, GreenfieldToolError
from scripts.run_greenfield_strands import _stage_outcome


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(path: Path, name: str) -> tuple[Path, str, str]:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "greenfield@example.invalid")
    _git(path, "config", "user.name", "Greenfield")
    (path / "behavior.txt").write_text("first\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    base = _git(path, "rev-parse", "HEAD")
    (path / "behavior.txt").write_text("first\nsecond\n", encoding="utf-8")
    _git(path, "commit", "-qam", "head")
    head = _git(path, "rev-parse", "HEAD")
    _git(path, "remote", "add", "origin", f"git@github.com:intacct/{name}.git")
    return path, base, head


def _step1(source: Path, base: str, head: str) -> dict:
    return build_report(
        {
            "schema_version": "0.2",
            "analysis_kind": "pr_impact_metadata",
            "repo_key": "source",
            "repository": "intacct/source",
            "pull_request": {
                "number": 7,
                "url": "https://github.com/intacct/source/pull/7",
                "title": "Change behavior",
                "base_revision": base,
                "target_revision": head,
            },
            "changed_files": [{"filename": "behavior.txt", "status": "modified"}],
            "linked_issues": [],
            "workflow_runs": [],
            "workflow_jobs": [],
            "check_runs": [],
            "evidence_status": {},
            "provenance": {"provider": "fixture", "endpoints": []},
        }
    )


def _context(tmp_path: Path) -> tuple[dict, Path]:
    source, base, head = _repo(tmp_path / "source", "source")
    explicit, _, _ = _repo(tmp_path / "explicit", "explicit-tests")
    discovery, _, _ = _repo(tmp_path / "discovery", "discovery-tests")
    manifest = tmp_path / "repos.yaml"
    manifest.write_text(
        f"""version: 1
repositories:
  - repo_key: source
    remote_url: git@github.com:intacct/source.git
    local_root: {source}
    enabled: true
    greenfield_analysis:
      role: source
      discovery_eligible: true
  - repo_key: discovery
    remote_url: git@github.com:intacct/discovery-tests.git
    local_root: {discovery}
    tracked_branch: main
    enabled: true
    greenfield_analysis:
      role: test
      discovery_eligible: true
  - repo_key: explicit
    remote_url: git@github.com:intacct/explicit-tests.git
    local_root: {explicit}
    tracked_branch: main
    enabled: true
    greenfield_analysis:
      role: test
      discovery_eligible: true
    pr_impact_contracts:
      - type: tests_behavior_of
        source_repository: intacct/explicit-tests
        target_repository: intacct/source
""",
        encoding="utf-8",
    )
    return (
        build_run_context(_step1(source, base, head), manifest, source_root=source),
        source,
    )


def test_capture_prioritizes_explicit_contract_then_discovery_scope(
    tmp_path: Path,
) -> None:
    context, _ = _context(tmp_path)
    assert validate_run_context(context) == []
    assert [row["priority"] for row in context["candidate_repositories"]] == [
        "explicit_contract",
        "discovery_screen",
    ]
    assert all(row["inspected_revision"] for row in context["candidate_repositories"])


def test_capture_promotes_repository_named_by_supplied_contract(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    contract = tmp_path / "explicit-contract.yaml"
    contract.write_text(
        "relations:\n  - consumer_repository: intacct/discovery-tests\n",
        encoding="utf-8",
    )
    source = context["source"]
    promoted = build_run_context(
        _step1(
            Path(source["local_root"]), source["base_revision"], source["head_revision"]
        ),
        context["manifest"]["path"],
        source_root=source["local_root"],
        contract_artifacts=[contract],
    )
    discovery = next(
        row
        for row in promoted["candidate_repositories"]
        if row["repository"] == "intacct/discovery-tests"
    )
    assert discovery["priority"] == "explicit_contract"


def test_run_context_records_explicit_execution_mode(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    execution = {
        "dry_run": True,
        "planner_mode": "default",
        "model": "shared-model",
        "base_url": "https://shared.example/v1",
    }

    explicit = build_run_context(
        _step1(
            Path(context["source"]["local_root"]),
            context["source"]["base_revision"],
            context["source"]["head_revision"],
        ),
        context["manifest"]["path"],
        source_root=context["source"]["local_root"],
        execution=execution,
    )

    assert validate_run_context(explicit) == []
    assert explicit["execution"] == execution


@pytest.mark.parametrize("mode", ["active", "shadow", "off"])
def test_run_context_rejects_legacy_planner_modes(tmp_path: Path, mode: str) -> None:
    context, _ = _context(tmp_path)
    context["execution"] = {"dry_run": True, "planner_mode": mode}

    assert "execution.planner_mode is invalid" in validate_run_context(context)


def test_toolbox_reads_only_captured_revision_and_records_evidence(
    tmp_path: Path,
) -> None:
    context, _ = _context(tmp_path)
    toolbox = GreenfieldToolbox(context)
    result = toolbox.read_source("intacct/source", "behavior.txt", 1, 2)
    assert result["status"] == "available"
    assert result["source_revision"] == context["source"]["head_revision"]
    assert "second" in result["excerpt"]
    assert toolbox.ledger()[0]["tool_call_id"] == result["tool_call_id"]
    assert toolbox.ledger()[0]["result"]["excerpt"] == result["excerpt"]


def test_toolbox_rejects_changed_and_oversized_captured_evidence(
    tmp_path: Path,
) -> None:
    context, _ = _context(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text('{"status": "available"}', encoding="utf-8")
    context = build_run_context(
        _step1(
            Path(context["source"]["local_root"]),
            context["source"]["base_revision"],
            context["source"]["head_revision"],
        ),
        context["manifest"]["path"],
        source_root=context["source"]["local_root"],
        evidence_artifacts=[evidence_path],
        tool_limits={"max_file_bytes": 10_000},
    )
    toolbox = GreenfieldToolbox(context)
    evidence_path.write_text('{"status": "tampered"}', encoding="utf-8")
    with pytest.raises(GreenfieldToolError, match="changed after Capture"):
        toolbox.read_evidence_artifact(evidence_path.name)

    oversized = tmp_path / "oversized.json"
    oversized.write_text('{"status": "' + ("x" * 20) + '"}', encoding="utf-8")
    bounded = build_run_context(
        _step1(
            Path(context["source"]["local_root"]),
            context["source"]["base_revision"],
            context["source"]["head_revision"],
        ),
        context["manifest"]["path"],
        source_root=context["source"]["local_root"],
        evidence_artifacts=[oversized],
        tool_limits={"max_file_bytes": 10},
    )
    with pytest.raises(GreenfieldToolError, match="exceeds max_file_bytes"):
        GreenfieldToolbox(bounded).read_evidence_artifact(oversized.name)


def test_analysis_retains_step3_repository_impacts(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={
            "potentially_affected_repositories": {
                "items": [
                    {
                        "target_repository": "intacct/explicit-tests",
                        "classification": "candidate",
                        "rationale": "The captured behavior is affected.",
                        "evidence": [],
                    }
                ]
            },
            "gaps": [],
        },
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
    )
    assert [row["repository"] for row in report["repository_impacts"]] == [
        "intacct/explicit-tests"
    ]


@pytest.mark.parametrize(
    ("source_trace", "expected_gap"),
    [
        ({"truncated": True}, "step1_5_trace_truncated"),
        (
            {"provenance": {"agent": {"continuation_attempts": 1}}},
            "step1_5_trace_continued",
        ),
        (
            {"provenance": {"agent": {"join_whitespace_trimmed": True}}},
            "step1_5_trace_join_trimmed",
        ),
    ],
)
def test_incomplete_source_trace_blocks_draft_and_records_gap(
    tmp_path: Path, source_trace: dict[str, object], expected_gap: str
) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
        agent_analysis={
            "repository_impacts": [
                {
                    "repository": "intacct/explicit-tests",
                    "evidence_state": "confirmed",
                    "rank": 1,
                    "rationale": "The captured behavior is affected.",
                    "evidence": [],
                }
            ],
            "actions": [
                {
                    "action_id": "a1",
                    "action_type": "update_existing_test",
                    "target_repository": "intacct/explicit-tests",
                    "evidence_state": "confirmed",
                    "draft_eligible": True,
                    "evidence": [],
                }
            ],
        },
        source_trace=source_trace,
    )
    assert report["status"] == "partial"
    assert expected_gap in report["gaps"]
    assert report["repository_impacts"][0]["evidence_state"] == "candidate"
    assert all(not row["draft_eligible"] for row in report["actions"])


def test_complete_source_trace_leaves_draft_eligibility_intact(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
        source_trace={
            "provenance": {
                "agent": {"continuation_attempts": 0, "join_whitespace_trimmed": False}
            }
        },
    )
    assert report["status"] == "complete"
    assert report["gaps"] == []


def test_strands_analysis_receives_bounded_tools(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    toolbox = GreenfieldToolbox(context)
    captured: dict[str, object] = {}

    def factory(model: str | None, *, tools: list[object]):
        captured["model"] = model
        captured["tools"] = tools

        def agent(prompt: str) -> str:
            captured["prompt"] = prompt
            return (
                '{"repository_impacts": [], "actions": [], "coverage": {}, '
                '"recommendation": "No supported impact.", "gaps": [], '
                '"agent": {"status": "complete"}}'
            )

        return agent

    result, ledger = run_strands_analysis(
        context,
        {"candidate_screen": []},
        toolbox,
        model="test-model",
        agent_factory=factory,
    )
    assert result["agent"]["status"] == "complete"
    assert len(captured["tools"]) == 7
    assert "explicit-contract candidates" in str(captured["prompt"])
    assert ledger == []


def test_analysis_requires_bound_evidence_for_strong_candidate(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    toolbox = GreenfieldToolbox(context)
    evidence = toolbox.read_source("intacct/explicit-tests", "behavior.txt", 1, 2)
    candidate = context["candidate_repositories"][0]
    action_id = "a" * 64
    agent = {
        "repository_impacts": [
            {
                "repository": candidate["repository"],
                "evidence_state": "strong_candidate",
                "rank": 1,
                "rationale": "A captured test behavior directly references the change.",
                "evidence": [
                    {"kind": "tool", "tool_call_id": evidence["tool_call_id"]}
                ],
            }
        ],
        "actions": [
            {
                "action_id": action_id,
                "action_type": "update_existing_test",
                "target_repository": candidate["repository"],
                "target_revision": candidate["inspected_revision"],
                "evidence_state": "strong_candidate",
                "scope": {
                    "allowed_paths": ["behavior.txt"],
                    "edit_operations": [
                        {
                            "path": "behavior.txt",
                            "old_text": "second",
                            "new_text": "updated",
                            "expected_occurrences": 1,
                        }
                    ],
                    "validation_plan": ["test behavior"],
                },
                "evidence": [
                    {"kind": "tool", "tool_call_id": evidence["tool_call_id"]}
                ],
                "rationale": "Update the existing test.",
                "completion_condition": "The targeted validation passes.",
                "draft_eligible": True,
            }
        ],
        "coverage": {},
        "gaps": [],
        "recommendation": "Create a validated draft.",
        "agent": {"status": "complete", "name": "strands"},
    }
    step5 = {
        "actions": [
            {
                "action_id": action_id,
                "action_type": "update_test_obligation",
                "status": "recommended",
                "target_repository": candidate["repository"],
                "scope": {},
                "evidence": [],
            }
        ],
        "gaps": [],
    }
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5=step5,
        agent_analysis=agent,
        tool_calls=toolbox.ledger(),
    )
    assert validate_analysis_report(report) == []
    assert report["actions"][0]["draft_eligible"] is True
    report["repository_impacts"][0]["evidence"] = []
    report.pop("report_sha256")
    report["report_sha256"] = artifact_sha256(report)
    assert "repository_impacts[0] lacks bound evidence" in validate_analysis_report(
        report
    )


def test_analysis_rejects_out_of_scope_strong_candidate(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    toolbox = GreenfieldToolbox(context)
    evidence = toolbox.read_source("intacct/explicit-tests", "behavior.txt", 1, 2)
    with pytest.raises(AnalysisReportError, match="outside captured scope"):
        build_analysis_report(
            context,
            step2={"gaps": []},
            step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
            step4={"coverage": {}, "gaps": []},
            step5={"actions": [], "gaps": []},
            agent_analysis={
                "repository_impacts": [
                    {
                        "repository": "intacct/not-captured",
                        "evidence_state": "strong_candidate",
                        "rank": 1,
                        "rationale": "unsupported target",
                        "evidence": [
                            {"kind": "tool", "tool_call_id": evidence["tool_call_id"]}
                        ],
                    }
                ],
                "actions": [],
                "coverage": {},
                "gaps": [],
                "agent": {"status": "complete"},
            },
            tool_calls=toolbox.ledger(),
        )


def test_analysis_rejects_strong_evidence_without_captured_revision(
    tmp_path: Path,
) -> None:
    context, _ = _context(tmp_path)
    candidate = context["candidate_repositories"][0]
    candidate["inspected_revision"] = None
    unsigned_context = dict(context)
    unsigned_context.pop("context_sha256")
    context["context_sha256"] = artifact_sha256(unsigned_context)
    result = {
        "status": "available",
        "repository": candidate["repository"],
        "source_revision": "a" * 40,
    }
    with pytest.raises(AnalysisReportError, match="captured repository revision"):
        build_analysis_report(
            context,
            step2={"gaps": []},
            step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
            step4={"coverage": {}, "gaps": []},
            step5={"actions": [], "gaps": []},
            agent_analysis={
                "repository_impacts": [
                    {
                        "repository": candidate["repository"],
                        "evidence_state": "strong_candidate",
                        "rank": 1,
                        "rationale": "Unsupported revision binding.",
                        "evidence": [{"tool_call_id": "call-1"}],
                    }
                ],
                "actions": [],
                "coverage": {},
                "gaps": [],
                "agent": {"status": "complete"},
            },
            tool_calls=[
                {
                    "tool_call_id": "call-1",
                    "result_sha256": artifact_sha256(result),
                    "result": result,
                }
            ],
        )


def test_repository_handbook_is_source_backed_and_resynchronizes(
    tmp_path: Path,
) -> None:
    source, _, head = _repo(tmp_path / "source", "source")
    contract = {
        "schema_version": "0.1",
        "artifact_kind": "generated_behavior_contract",
        "repository": "intacct/source",
        "revision": head,
        "relations": [
            {
                "interface_id": "behavior.example",
                "relationship_type": "behavior_contract",
                "status": "active",
                "description": "Example behavior",
                "source_paths": ["behavior.txt"],
                "source_symbols": ["example"],
            }
        ],
        "generation": {"edges": [{"source_path": "behavior.txt", "source_line": 2}]},
    }
    handbook = build_repository_handbook(contract, source)
    assert validate_repository_handbook(handbook) == []
    assert handbook["sections"]["behavior:behavior.example"]["level"] == "L3"
    updated = resynchronize_repository_handbook(
        handbook, contract, source, changed_paths=["behavior.txt"]
    )
    assert (
        updated["resynchronization"]["previous_handbook_sha256"]
        == handbook["handbook_sha256"]
    )


class _Publisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, endpoint: str, body: dict | None = None):
        self.calls.append((method, endpoint, body))
        if "/check-runs?" in endpoint and method == "GET":
            return {"check_runs": []}
        if "/comments?" in endpoint:
            return []
        return {"id": len(self.calls)}


def test_publication_uses_one_check_and_one_marker_comment(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
    )
    publication = build_publication(report, artifact_bundle="bundle")
    publisher = _Publisher()
    result = publish_github(publication, publisher)
    assert result["status"] == "published"
    assert publication["comment"]["marker"] in publication["comment"]["body"]
    assert [method for method, _, _ in publisher.calls].count("POST") == 2


def test_publication_updates_without_create_only_check_fields(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
    )
    publication = build_publication(report, artifact_bundle="bundle")

    class ExistingPublisher(_Publisher):
        def request(self, method: str, endpoint: str, body: dict | None = None):
            self.calls.append((method, endpoint, body))
            if "/check-runs?" in endpoint and method == "GET":
                return {
                    "check_runs": [
                        {
                            "id": 10,
                            "name": publication["check"]["name"],
                            "external_id": publication["check"]["external_id"],
                        }
                    ]
                }
            if "/comments?" in endpoint:
                return [
                    {
                        "id": 20,
                        "body": publication["comment"]["marker"],
                    }
                ]
            return {"id": 10 if "check-runs" in endpoint else 20}

    publisher = ExistingPublisher()
    publish_github(publication, publisher)
    check_update = next(
        body
        for method, endpoint, body in publisher.calls
        if method == "PATCH" and "check-runs" in endpoint
    )
    assert "head_sha" not in check_update
    assert "external_id" not in check_update


def test_publication_digest_and_pagination_are_fail_closed(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
    )
    publication = build_publication(report, artifact_bundle="bundle")
    assert validate_publication(publication) == []
    tampered = dict(publication)
    tampered["publication_sha256"] = "0" * 64
    assert any("publication_sha256" in error for error in validate_publication(tampered))

    class DuplicatePagedPublisher(_Publisher):
        def request(self, method: str, endpoint: str, body: dict | None = None):
            self.calls.append((method, endpoint, body))
            if "/check-runs?" in endpoint and method == "GET":
                page = 2 if "page=2" in endpoint else 1
                rows = [{"id": index, "name": "other", "external_id": str(index)} for index in range(100)]
                rows[0] = {"id": 998, "name": publication["check"]["name"], "external_id": publication["check"]["external_id"]}
                if page == 2:
                    rows = [{"id": 999, "name": publication["check"]["name"], "external_id": publication["check"]["external_id"]}]
                return {"check_runs": rows}
            if "/comments?" in endpoint and method == "GET":
                return []
            raise AssertionError("unexpected GitHub request")

    with pytest.raises(ValueError, match="multiple Greenfield checks"):
        publish_github(publication, DuplicatePagedPublisher())


def test_publication_contract_rejects_malformed_identity_and_accepts_legacy_replay(
    tmp_path: Path,
) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
    )
    publication = build_publication(report, artifact_bundle="bundle")
    malformed = dict(publication)
    malformed["source"] = {**publication["source"], "repository": "x"}
    malformed["check"] = {**publication["check"], "external_id": "not-a-sha"}
    malformed["planning_status"] = "bogus"
    malformed["publication_sha256"] = artifact_sha256(_publication_unsigned(malformed))
    malformed["check"]["publication_sha256"] = malformed["publication_sha256"]
    malformed["comment"] = {**publication["comment"], "publication_sha256": malformed["publication_sha256"]}
    errors = validate_publication(malformed)
    assert "source.repository must be owner/repository" in errors
    assert "check.external_id must be a SHA-256" in errors
    assert "planning_status is invalid" in errors

    legacy = dict(publication)
    legacy["check"] = {key: value for key, value in publication["check"].items() if key != "publication_sha256"}
    legacy["comment"] = {key: value for key, value in publication["comment"].items() if key != "publication_sha256"}
    legacy.pop("draft_pr")
    legacy.pop("canonical_analysis")
    legacy["publication_sha256"] = artifact_sha256(_publication_unsigned(legacy))
    assert validate_publication(legacy)
    assert validate_publication(legacy, allow_legacy_replay=True) == []


def test_projection_consistency_rejects_semantic_mismatch(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    report = build_analysis_report(
        context,
        step2={"gaps": []},
        step3={"potentially_affected_repositories": {"items": []}, "gaps": []},
        step4={"coverage": {}, "gaps": []},
        step5={"actions": [], "gaps": []},
    )
    projection = {
        "provenance": {"analysis_report_sha256": report["report_sha256"]},
        "canonical_analysis": canonical_analysis_projection(report),
    }
    assert validate_projection_consistency(projection, report) == []
    projection["canonical_analysis"]["actions"] = [{"action_id": "different"}]
    assert "canonical_analysis does not exactly match canonical analysis" in validate_projection_consistency(projection, report)


def test_unknown_stage_status_fails_closed() -> None:
    assert _stage_outcome({"status": "unrecognized"}) == "failed"
    assert _stage_outcome(None) == "failed"


def test_draft_authorizer_uses_validation_not_owner_approval() -> None:
    step7 = {
        "status": "validated",
        "validation_fingerprint": "b" * 64,
    }
    request = {"artifacts": {"step7_report_sha256": artifact_sha256(step7)}}
    result = ValidatedDraftAuthorizer().authorize(request, {}, step7)
    assert result["authorized"] is True
    assert result["evidence"]["kind"] == "validated_step7_report"
