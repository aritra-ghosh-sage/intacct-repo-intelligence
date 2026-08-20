from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_repository_evidence,
)
from greenfield.semantic_index import build_semantic_index_from_files
from greenfield.step2_candidates import resolve_candidates
from greenfield.step2_contract import EvidenceError, load_ci_evidence, load_contract
from scripts.validate_greenfield_step2 import validate

TARGET = "a" * 40
CONSUMER = "b" * 40


def step1() -> dict:
    return {
        "schema_version": "0.5",
        "analysis_kind": "pr_impact_step_1",
        "status": "partial",
        "input": {
            "repo_key": "ia-app",
            "target_revision": TARGET,
        },
        "changed_files": [
            {"path": "app/source/company/CompanyConfig.cls", "status": "modified"}
        ],
    }


def write_contract(path: Path, revision: str = TARGET) -> None:
    path.write_text(
        f"""schema_version: '0.1'
repository: ia-app
revision: {revision}
relations:
  - interface_id: company.config.general-ledger-preference
    consumer_repository: ia-restapi-automation-tests
    relationship_type: api_contract
    source_paths:
      - app/source/company/CompanyConfig.cls
    status: active
    owner: company-platform
""",
        encoding="utf-8",
    )


def write_ci(path: Path, source_revision: str = TARGET) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "evidence_id": "run-123",
                "repository": "ia-restapi-automation-tests",
                "commit_sha": CONSUMER,
                "source_repository": "ia-app",
                "source_revision": source_revision,
                "interface_id": "company.config.general-ledger-preference",
                "status": "available",
                "tests": [{"id": "gl-preference", "path": "tests/gl.py"}],
            }
        ),
        encoding="utf-8",
    )


def test_exact_contract_is_confirmed_and_ci_is_retained(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    ci_path = tmp_path / "ci.json"
    write_contract(contract_path)
    write_ci(ci_path)

    report = resolve_candidates(
        step1(), [load_contract(contract_path)], [load_ci_evidence(ci_path)]
    )

    assert report["status"] == "complete"
    assert report["blast_radius"] == "multi_repo"
    assert [row["classification"] for row in report["candidates"]] == [
        "confirmed",
        "candidate",
    ]
    assert report["candidates"][0]["evidence"][0]["kind"] == "contract"
    assert report["candidates"][1]["tests"] == [
        {"id": "gl-preference", "path": "tests/gl.py"}
    ]
    assert validate(report) == []


def test_stale_contract_and_ci_remain_explicit_gaps(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    ci_path = tmp_path / "ci.json"
    write_contract(contract_path, "c" * 40)
    write_ci(ci_path, "d" * 40)

    report = resolve_candidates(
        step1(), [load_contract(contract_path)], [load_ci_evidence(ci_path)]
    )

    assert report["status"] == "partial"
    assert report["candidates"] == []
    assert any(gap.startswith("contract:stale") for gap in report["gaps"])
    assert any(gap.startswith("ci:stale") for gap in report["gaps"])
    assert report["blast_radius"] == "unknown"
    assert validate(report) == []


def test_same_names_do_not_create_a_relationship(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    write_contract(contract_path)
    contract = load_contract(contract_path)
    contract["repository"] = "another-repository"

    report = resolve_candidates(step1(), [contract])

    assert report["candidates"] == []
    assert report["gaps"] == ["contract:source_repository_mismatch:another-repository"]


def test_contract_rejects_wildcard_paths(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    write_contract(contract_path)
    text = contract_path.read_text(encoding="utf-8").replace(
        "CompanyConfig.cls", "*.cls"
    )
    contract_path.write_text(text, encoding="utf-8")

    with pytest.raises(EvidenceError, match="exact paths"):
        load_contract(contract_path)


def test_repeated_resolution_is_byte_deterministic(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    ci_path = tmp_path / "ci.json"
    write_contract(contract_path)
    write_ci(ci_path)
    contract = load_contract(contract_path)
    ci = load_ci_evidence(ci_path)

    first = resolve_candidates(step1(), [contract], [ci])
    second = resolve_candidates(step1(), [contract], [ci])

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )


def test_semantic_index_supports_candidate_without_proving_ci() -> None:
    semantic = build_semantic_index_from_files(
        {
            "app/source/gl/glaccount.ent": "$kSchemas['glaccount'] = array('module' => 'gl');",
            "app/source/openapispec/gl/models/objects.general-ledger.account.s1.schema.yaml": "x-mappedTo: glaccount\n",
        },
        repository="ia-app",
        revision=TARGET,
    )
    semantic["evidence_path"] = "semantic.json"
    contract = {
        "repository": "ia-app",
        "revision": TARGET,
        "relations": [
            {
                "interface_id": "api_object:general-ledger/account",
                "consumer_repository": "ia-restapi-automation-tests",
                "relationship_type": "api_contract",
                "source_paths": ["app/source/company/CompanyConfig.cls"],
                "status": "active",
            }
        ],
        "evidence": {"path": "contract.yaml", "sha256": "c" * 64},
    }
    semantic_step1 = {
        **step1(),
        "changed_files": [
            {
                "path": "app/source/openapispec/gl/models/objects.general-ledger.account.s1.schema.yaml",
                "status": "modified",
            }
        ],
    }

    report = resolve_candidates(
        semantic_step1,
        contracts=[contract],
        semantic_indexes=[semantic],
    )

    assert report["candidates"][0]["classification"] == "candidate"
    assert report["candidates"][0]["reason"] == "semantic_index_supports_contract"
    assert report["candidates"][0]["evidence"][0]["kind"] == "semantic_index"
    assert validate(report) == []


def test_semantic_index_does_not_resurrect_a_stale_contract() -> None:
    semantic_path = (
        "app/source/openapispec/gl/models/objects.general-ledger.account.s1.schema.yaml"
    )
    semantic = build_semantic_index_from_files(
        {
            "app/source/gl/glaccount.ent": "$kSchemas['glaccount'] = array();",
            semantic_path: "x-mappedTo: glaccount\n",
        },
        repository="ia-app",
        revision=TARGET,
    )
    semantic["evidence_path"] = "semantic.json"
    stale_contract = {
        "repository": "ia-app",
        "revision": "b" * 40,
        "relations": [
            {
                "interface_id": "api_object:general-ledger/account",
                "consumer_repository": "ia-restapi-automation-tests",
                "relationship_type": "api_contract",
                "source_paths": [semantic_path],
                "status": "active",
            }
        ],
        "evidence": {"path": "contract.yaml", "sha256": "c" * 64},
    }
    semantic_step1 = {
        **step1(),
        "changed_files": [{"path": semantic_path, "status": "modified"}],
    }

    report = resolve_candidates(
        semantic_step1,
        contracts=[stale_contract],
        semantic_indexes=[semantic],
    )

    assert report["candidates"] == []
    assert any(gap.startswith("contract:stale") for gap in report["gaps"])


def provider_for(workflow_path: str, workflow_text: str, *, runs=None, artifacts=None):
    responses = {
        "repos/intacct/example": {
            "full_name": "intacct/example",
            "default_branch": "main",
        },
        "repos/intacct/example/git/ref/heads/main": {"object": {"sha": "e" * 40}},
        "repos/intacct/example/git/trees/" + "e" * 40 + "?recursive=1": {
            "tree": [
                {"path": workflow_path, "type": "blob"},
                {"path": "features/example.feature", "type": "blob"},
                {"path": "testscripts/example.xml", "type": "blob"},
            ]
        },
        f"repos/intacct/example/contents/{workflow_path}?ref=" + "e" * 40: {
            "content": base64.b64encode(workflow_text.encode()).decode()
        },
        "repos/intacct/example/actions/runs?head_sha=" + TARGET + "&per_page=100": {
            "workflow_runs": runs or []
        },
        "repos/intacct/example/commits/" + TARGET + "/check-runs?per_page=100": {
            "check_runs": []
        },
        "repos/intacct/example/actions/artifacts?per_page=100": {
            "artifacts": artifacts or []
        },
    }
    for run in runs or []:
        responses[
            f"repos/intacct/example/actions/runs/{run['id']}/jobs?per_page=100"
        ] = {"jobs": []}

    def provider(endpoint: str):
        if endpoint not in responses:
            raise AssertionError(f"unexpected endpoint: {endpoint}")
        return responses[endpoint]

    return provider


def test_rest_inventory_without_linked_artifact_is_a_candidate(tmp_path: Path) -> None:
    workflow = (
        Path(__file__).parent / "fixtures/greenfield/rest_workflow.yml"
    ).read_text()
    evidence = collect_repository_evidence(
        "intacct/example",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider_for(".github/workflows/rest.yml", workflow),
    )
    report = resolve_candidates(step1(), inventory_evidence=[evidence])

    assert report["candidates"][0]["classification"] == "candidate"
    assert report["candidates"][0]["reason"] == "workflow_has_no_test_execution"
    assert "ci_artifact_unavailable:intacct/example" in report["gaps"]
    assert report["candidates"][0]["evidence"][0]["kind"] == "repository_inventory"
    assert validate(report) == []


def test_gateway_pass_only_workflow_never_proves_test_execution() -> None:
    workflow = (
        Path(__file__).parent / "fixtures/greenfield/gateway_statuscheck.yml"
    ).read_text()
    evidence = collect_repository_evidence(
        "intacct/example",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider_for(".github/workflows/StatusCheck.yml", workflow),
    )
    report = resolve_candidates(step1(), inventory_evidence=[evidence])

    candidate = report["candidates"][0]
    assert candidate["classification"] == "candidate"
    assert candidate["reason"] == "workflow_has_no_test_execution"
    assert "workflow_has_no_test_execution:intacct/example" in report["gaps"]


def test_truncated_tree_is_retained_as_an_inventory_gap() -> None:
    workflow = "name: status\njobs: {}\n"
    provider = provider_for(".github/workflows/status.yml", workflow)
    original = provider

    def truncated_provider(endpoint: str):
        response = original(endpoint)
        if endpoint.endswith("?recursive=1"):
            response = {**response, "truncated": True}
        return response

    evidence = collect_repository_evidence(
        "intacct/example",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=truncated_provider,
    )
    report = resolve_candidates(step1(), inventory_evidence=[evidence])

    assert any("response_truncated" in gap for gap in report["gaps"])
    assert report["status"] == "partial"


def test_validator_rejects_confirmed_inventory_evidence() -> None:
    workflow = "name: status\njobs: {}\n"
    evidence = collect_repository_evidence(
        "intacct/example",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider_for(".github/workflows/status.yml", workflow),
    )
    report = resolve_candidates(step1(), inventory_evidence=[evidence])
    report["candidates"][0]["classification"] = "confirmed"

    assert "repository inventory evidence can only classify a candidate" in validate(
        report
    )


def test_artifact_is_joined_only_to_the_matching_workflow_run() -> None:
    workflow = "name: test\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: mvn test\n"
    runs = [{"id": 7, "head_sha": TARGET, "name": "test"}]
    artifacts = [
        {"id": 8, "name": "step2-evidence", "workflow_run": {"id": 7}},
        {"id": 9, "name": "other-run", "workflow_run": {"id": 99}},
    ]
    evidence = collect_repository_evidence(
        "intacct/example",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider_for(
            ".github/workflows/test.yml", workflow, runs=runs, artifacts=artifacts
        ),
    )

    assert evidence["artifact_status"] == "available"
    assert [artifact["id"] for artifact in evidence["artifacts"]] == [8]


def test_repository_access_failure_is_fail_closed() -> None:
    def provider(endpoint: str):
        raise RepositoryEvidenceError("private repository access denied")

    with pytest.raises(RepositoryEvidenceError, match="access denied"):
        collect_repository_evidence(
            "intacct/example",
            source_repository="ia-app",
            source_revision=TARGET,
            provider=provider,
        )


def test_inventory_collection_is_byte_deterministic() -> None:
    workflow = "name: status\njobs:\n  status:\n    steps:\n      - run: echo pass\n"
    provider = provider_for(".github/workflows/status.yml", workflow)
    first = collect_repository_evidence(
        "intacct/example",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider,
    )
    second = collect_repository_evidence(
        "intacct/example",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider,
    )

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
