from __future__ import annotations

import json
from pathlib import Path

import pytest

from greenfield.semantic_contract import finalize_index
from greenfield.step2_candidates import resolve_candidates
from greenfield.step3_outcome import (
    OutcomeError,
    assemble_outcome,
    load_related_pr_evidence,
)
from scripts.validate_greenfield_step3 import validate

TARGET = "a" * 40


def step1(changed_path: str = "app/source/company/CompanyConfig.cls") -> dict:
    return {
        "schema_version": "0.5",
        "analysis_kind": "pr_impact_step_1",
        "status": "partial",
        "input": {"repo_key": "ia-app", "target_revision": TARGET},
        "changed_files": [{"path": changed_path, "status": "modified"}],
    }


def step2(*, candidate: bool = True, systemic: bool = False) -> dict:
    contracts = []
    if candidate:
        relation_type = "shared_schema" if systemic else "api_contract"
        contracts = [
            {
                "repository": "ia-app",
                "revision": TARGET,
                "relations": [
                    {
                        "interface_id": "company.config.general-ledger-preference",
                        "owner_repository": "ia-app",
                        "consumer_repository": "intacct/ia-restapi-automation-tests",
                        "relationship_type": relation_type,
                        "source_paths": ["app/source/company/CompanyConfig.cls"],
                        "status": "active",
                        "owner": "company-platform",
                    }
                ],
                "evidence": {"path": "contract.yaml", "sha256": "c" * 64},
            }
        ]
    return resolve_candidates(step1(), contracts=contracts)


def inventory_step2() -> dict:
    return resolve_candidates(
        step1(),
        inventory_evidence=[
            {
                "schema_version": "0.1",
                "evidence_type": "repository_inventory",
                "repository": "intacct/ia-gwdata-gl",
                "source_repository": "ia-app",
                "source_revision": TARGET,
                "inspected_revision": "b" * 40,
                "status": "available",
                "workflow_paths": [".github/workflows/tests.yml"],
                "inventory_paths": ["tests/a.csv", "tests/b.csv"],
                "workflows": [
                    {"classification": "metadata_only", "has_test_execution": False}
                ],
                "workflow_runs": [],
                "check_runs": [],
                "artifacts": [],
                "gaps": [],
                "artifact_status": "empty",
                "ci_linkage": {
                    "status": "unavailable",
                    "source_repository": "ia-app",
                    "source_revision": TARGET,
                },
                "provenance": {"response_sha256": "e" * 64},
            }
        ],
    )


def related_file(tmp_path: Path, *, revision: str = TARGET) -> Path:
    path = tmp_path / "related.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "evidence_type": "related_pull_requests",
                "source_repository": "ia-app",
                "source_revision": revision,
                "source_pr_number": 49156,
                "pull_requests": [
                    {
                        "repository": "intacct/ia-restapi-automation-tests",
                        "number": 49201,
                        "state": "open",
                        "head_sha": "b" * 40,
                        "base_sha": "c" * 40,
                        "relation_type": "declared_interface_follow_up",
                        "evidence": {"id": "review:49156:49201"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_candidate_outcome_preserves_owner_and_test_unavailability() -> None:
    report = assemble_outcome(step2())
    assert report["blast_radius"] == "multi_repo"
    assert (
        report["potentially_affected_repositories"]["items"][0]["classification"]
        == "confirmed"
    )
    assert report["owners"]["items"][0]["owner"] == "company-platform"
    assert report["owners"]["items"][0]["owner_repository"] == "ia-app"
    assert report["test_suites"]["items"][0]["status"] == "unavailable"
    assert report["related_pull_requests"]["status"] == "not_modelled"
    assert validate(report) == []


def test_malformed_test_evidence_is_not_reported_as_coverage() -> None:
    source = step2()
    source["candidates"][0]["tests"] = [{"unexpected": "shape"}]
    report = assemble_outcome(source)
    assert report["test_suites"]["status"] == "partial"
    assert report["test_suites"]["items"][0]["status"] == "unavailable"
    assert "test_suites_unavailable:malformed_test_evidence" in report["gaps"]
    assert validate(report) == []


def test_local_outcome_has_explicit_surfaces() -> None:
    semantic = finalize_index(
        repository="ia-app",
        revision=TARGET,
        nodes=[],
        edges=[],
        diagnostics=[],
        extractor_versions={"test": "1"},
    )
    related = {
        "source_repository": "ia-app",
        "source_revision": TARGET,
        "source_pr_number": 49156,
        "evidence_path": "related.json",
        "artifact_sha256": "d" * 64,
        "pull_requests": [],
    }
    report = assemble_outcome(
        step2(candidate=False), semantic_index=semantic, related_pr_evidence=related
    )
    assert report["blast_radius"] == "local"
    assert report["direct_components"]["items"][0]["kind"] == "file"
    assert report["impact"]["status"] == "available"
    assert report["related_pull_requests"]["status"] == "available"
    assert validate(report) == []


def test_systemic_requires_explicit_evidence() -> None:
    report = assemble_outcome(step2(systemic=True))
    assert report["blast_radius"] == "systemic"
    assert validate(report) == []


def test_semantic_components_use_exact_changed_path_evidence() -> None:
    semantic = finalize_index(
        repository="ia-app",
        revision=TARGET,
        nodes=[
            {
                "key": "api_object:company/config",
                "kind": "api_object",
                "identity": "company/config",
            }
        ],
        edges=[
            {
                "source": "api_object:company/config",
                "target": None,
                "kind": "changed_surface",
                "resolution": "resolved_exact",
                "evidence": [{"source_path": "app/source/company/CompanyConfig.cls"}],
            }
        ],
        diagnostics=[],
        extractor_versions={"test": "1"},
    )
    report = assemble_outcome(step2(candidate=False), semantic_index=semantic)
    identities = {item["identity"] for item in report["direct_components"]["items"]}
    assert "company/config" in identities
    assert report["direct_components"]["semantic_evidence"] == "available"
    assert validate(report) == []


def test_inventory_candidates_are_compact_repository_scope() -> None:
    report = assemble_outcome(inventory_step2())

    assert (
        report["potentially_affected_repositories"]["items"][0]["repository"]
        == "intacct/ia-gwdata-gl"
    )
    assert report["interfaces"]["status"] == "not_modelled"
    assert report["owners"]["status"] == "not_modelled"
    assert report["test_suites"]["status"] == "not_modelled"
    item = report["impact"]["items"][0]
    assert item["scope"] == "repository"
    assert "interface_id" not in item
    assert item["observation"] == {
        "inspected_revision": "b" * 40,
        "source_revision": TARGET,
        "inventory_path_count": 2,
        "workflow_path_count": 1,
        "workflow_count": 1,
        "artifact_status": "empty",
        "ci_linkage_status": "unavailable",
        "response_sha256": "e" * 64,
    }
    assert not {
        "inventory_paths",
        "workflow_paths",
        "workflows",
        "changed_paths",
    } & set(item)
    assert validate(report) == []


def test_candidate_semantic_edges_are_not_direct_components() -> None:
    semantic = finalize_index(
        repository="ia-app",
        revision=TARGET,
        nodes=[
            {"key": "component:exact", "kind": "component", "identity": "exact"},
            {
                "key": "component:candidate",
                "kind": "component",
                "identity": "candidate",
            },
        ],
        edges=[
            {
                "source": "component:exact",
                "target": None,
                "kind": "changed_surface",
                "resolution": "resolved_exact",
                "evidence": [{"source_path": "app/source/company/CompanyConfig.cls"}],
            },
            {
                "source": "component:candidate",
                "target": None,
                "kind": "changed_surface",
                "resolution": "candidate_static",
                "evidence": [{"source_path": "app/source/company/CompanyConfig.cls"}],
            },
        ],
        diagnostics=[],
        extractor_versions={"test": "1"},
    )
    report = assemble_outcome(step2(candidate=False), semantic_index=semantic)
    identities = {item["identity"] for item in report["direct_components"]["items"]}

    assert "exact" in identities
    assert "candidate" not in identities
    assert "semantic_components_not_promoted:1" in report["gaps"]
    assert validate(report) == []


def test_inventory_only_inaccessible_evidence_is_unknown() -> None:
    source = step2(candidate=False)
    source["gaps"] = ["repository_access_unavailable:intacct/unknown"]

    report = assemble_outcome(source)

    assert report["blast_radius"] == "unknown"
    assert validate(report) == []


def test_duplicate_candidates_are_aggregated() -> None:
    source = step2()
    duplicate_surface = dict(source["candidates"][0])
    duplicate_surface.update(
        relationship_type="ci_observed",
        reason="ci_observed_exact_source_revision",
        evidence=[
            {
                "kind": "ci",
                "evidence_id": "ci:49156",
                "path": "ci.json",
                "sha256": "d" * 64,
            }
        ],
    )
    source["candidates"].append(duplicate_surface)
    source["candidates"] = sorted(
        source["candidates"],
        key=lambda candidate: (
            candidate["target_repository"],
            candidate["interface_id"],
            candidate["relationship_type"],
            candidate["classification"],
        ),
    )

    report = assemble_outcome(source)

    assert len(report["impact"]["items"]) == 2
    assert len(report["interfaces"]["items"]) == 1
    assert validate(report) == []


def test_validator_rejects_raw_inventory_fields_in_impact() -> None:
    report = assemble_outcome(inventory_step2())
    report["impact"]["items"][0]["inventory_paths"] = ["tests/a.csv"]

    errors = validate(report)

    assert "impact must not contain raw inventory fields: inventory_paths" in errors


def test_related_prs_are_source_revision_pinned(tmp_path: Path) -> None:
    evidence = load_related_pr_evidence(related_file(tmp_path))
    report = assemble_outcome(step2(), related_pr_evidence=evidence)
    assert report["related_pull_requests"]["status"] == "available"
    assert report["related_pull_requests"]["source_pr_number"] == 49156
    assert report["related_pull_requests"]["items"][0]["number"] == 49201
    assert "related_pull_requests_not_modelled" not in report["gaps"]
    assert validate(report) == []


def test_validator_rejects_tampered_related_pr_surface(tmp_path: Path) -> None:
    evidence = load_related_pr_evidence(related_file(tmp_path))
    report = assemble_outcome(step2(), related_pr_evidence=evidence)
    report["related_pull_requests"]["items"][0]["state"] = "closed"
    errors = validate(report)
    assert "related_pull_requests[0].state is invalid" in errors


def test_related_pr_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    evidence = load_related_pr_evidence(related_file(tmp_path, revision="d" * 40))
    with pytest.raises(OutcomeError, match="revision"):
        assemble_outcome(step2(), related_pr_evidence=evidence)


def test_outcome_is_deterministic() -> None:
    first = assemble_outcome(step2())
    second = assemble_outcome(step2())
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
