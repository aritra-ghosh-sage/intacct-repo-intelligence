from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from scripts import run_greenfield_codex
from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_repository_evidence,
    collect_target_evidence,
)
from greenfield.semantic_index import build_semantic_index_from_files
from greenfield.step2_candidates import _evidence_score, resolve_candidates
from greenfield.step2_contract import EvidenceError, load_ci_evidence, load_contract
from greenfield.step2_likelihood import build_source_anchors, rank_likely_tests
from scripts.trace_greenfield_step2 import _manifest_candidates, _unavailable_inventory
from scripts.validate_greenfield_step2 import validate

TARGET = "a" * 40
CONSUMER = "b" * 40
DOWNSTREAM = "e" * 40


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


def test_evidence_score_is_explainable_and_not_probability() -> None:
    score = _evidence_score(
        {
            "declared_relationship_type": "behavior_contract",
            "evidence": [{"kind": "contract"}, {"kind": "ci"}],
        }
    )
    assert score == {
        "score": 100,
        "rule_set_version": "0.1",
        "components": {
            "behavior_contract": 20,
            "exact_contract": 60,
            "source_revision_ci": 25,
        },
        "meaning": "evidence_strength_not_probability",
    }


def test_validator_rejects_tampered_evidence_score(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    write_contract(contract_path)
    report = resolve_candidates(
        step1(),
        [load_contract(contract_path)],
        include_evidence_scores=True,
    )
    assert validate(report) == []
    report["candidates"][0]["evidence_score"]["score"] = 0
    assert any("evidence_score" in error for error in validate(report))


def test_manifest_candidates_include_greenfield_discovery_eligible_test_repository(tmp_path: Path) -> None:
    manifest = tmp_path / "repos.yaml"
    manifest.write_text(
        """version: 1
repositories:
  - repo_key: ia-main
    remote_url: git@github.com:intacct/ia-app.git
    local_root: /tmp/ia-app
    tracked_branch: main
    enabled: true
    profile: intacct_app
    greenfield_analysis:
      role: source
      discovery_eligible: true
  - repo_key: ia-restapi-automation-tests
    remote_url: git@github.com:intacct/ia-restapi-automation-tests.git
    local_root: /tmp/ia-restapi-automation-tests
    tracked_branch: main
    enabled: true
    profile: rest_automation
    greenfield_analysis:
      role: test
      discovery_eligible: true
      test_roots:
        - features
      test_formats:
        - gherkin
""",
        encoding="utf-8",
    )
    assert _manifest_candidates(manifest, "intacct/ia-app", "ia-app") == [
        "intacct/ia-restapi-automation-tests"
    ]


def test_manifest_candidates_exclude_disabled_test_repository(tmp_path: Path) -> None:
    manifest = tmp_path / "repos.yaml"
    manifest.write_text(
        """version: 1
repositories:
  - repo_key: ia-main
    remote_url: git@github.com:intacct/ia-app.git
    local_root: /tmp/ia-app
    tracked_branch: main
    enabled: true
    profile: intacct_app
    greenfield_analysis:
      role: source
      discovery_eligible: true
  - repo_key: ia-restapi-automation-tests
    remote_url: git@github.com:intacct/ia-restapi-automation-tests.git
    local_root: /tmp/ia-restapi-automation-tests
    tracked_branch: main
    enabled: false
    profile: rest_automation
    greenfield_analysis:
      role: test
      discovery_eligible: true
      test_roots:
        - features
      test_formats:
        - gherkin
""",
        encoding="utf-8",
    )
    assert _manifest_candidates(manifest, "intacct/ia-app", "ia-app") == []


def test_run_greenfield_codex_manifest_candidates_exclude_disabled_test_repository(tmp_path: Path) -> None:
    manifest = tmp_path / "repos.yaml"
    manifest.write_text(
        """version: 1
repositories:
  - repo_key: ia-main
    remote_url: git@github.com:intacct/ia-app.git
    local_root: /tmp/ia-app
    tracked_branch: main
    enabled: true
    profile: intacct_app
    greenfield_analysis:
      role: source
      discovery_eligible: true
  - repo_key: ia-restapi-automation-tests
    remote_url: git@github.com:intacct/ia-restapi-automation-tests.git
    local_root: /tmp/ia-restapi-automation-tests
    tracked_branch: main
    enabled: false
    profile: rest_automation
    greenfield_analysis:
      role: test
      discovery_eligible: true
      test_roots:
        - features
      test_formats:
        - gherkin
""",
        encoding="utf-8",
    )
    assert run_greenfield_codex._manifest_candidates(manifest, "intacct/ia-app", "ia-app") == []


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


def test_behavior_contract_requires_and_preserves_protected_behavior(tmp_path: Path) -> None:
    contract_path = tmp_path / "behavior.yaml"
    contract_path.write_text(
        f"""schema_version: '0.1'
repository: ia-app
revision: {TARGET}
relations:
  - interface_id: behavior:gl-allocation-recalculation
    consumer_repository: ia-gwdata-gl
    relationship_type: behavior_contract
    source_paths:
      - app/source/gl/GLBatchManager.cls
    source_symbols:
      - GLBatchManager::glTranslateApplyAllocation
    protected_behavior: allocation split totals are recalculated after apply
    entry_surfaces:
      - xml_api
    status: active
""",
        encoding="utf-8",
    )
    relation = load_contract(contract_path)["relations"][0]
    assert relation["relationship_type"] == "behavior_contract"
    assert relation["protected_behavior"].startswith("allocation split")
    assert relation["entry_surfaces"] == ["xml_api"]


def test_available_ci_without_execution_binding_remains_candidate(tmp_path: Path) -> None:
    ci_path = tmp_path / "ci.json"
    write_ci(ci_path)
    source = step1()
    source["input"]["pr_number"] = 49137
    source["input"]["evidence_profile"] = "trust_foundation_v1"
    report = resolve_candidates(source, ci_evidence=[load_ci_evidence(ci_path)])
    assert report["confidence"]["band"] == "candidate"
    assert report["confidence"]["components"]["ci_execution"] == "unbound"


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


def test_semantic_index_revision_mismatch_is_observable() -> None:
    semantic = build_semantic_index_from_files(
        {"app/source/gl/glaccount.ent": "$kSchemas['glaccount'] = array();"},
        repository="ia-app",
        revision="b" * 40,
    )
    semantic["evidence_path"] = "semantic.json"

    report = resolve_candidates(step1(), semantic_indexes=[semantic])

    assert "semantic_index:stale:semantic.json" in report["gaps"]


def test_semantic_index_no_changed_edge_is_observable() -> None:
    semantic = build_semantic_index_from_files(
        {
            "app/source/gl/glaccount.ent": "$kSchemas['glaccount'] = array();",
            "app/source/openapispec/gl/models/objects.general-ledger.account.s1.schema.yaml": "x-mappedTo: glaccount\n",
        },
        repository="ia-app",
        revision=TARGET,
    )
    semantic["evidence_path"] = "semantic.json"

    report = resolve_candidates(
        step1(),
        semantic_indexes=[semantic],
    )

    assert "semantic_index_no_changed_edge:semantic.json" in report["gaps"]


def test_semantic_index_unmatched_interface_is_observable() -> None:
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
    contract = {
        "repository": "ia-app",
        "revision": TARGET,
        "relations": [
            {
                "interface_id": "api_object:other-object",
                "consumer_repository": "intacct/consumer",
                "status": "active",
                "source_paths": ["app/source/other.cls"],
            }
        ],
    }
    semantic_step1 = {
        **step1(),
        "changed_files": [{"path": semantic_path, "status": "modified"}],
    }

    report = resolve_candidates(
        semantic_step1,
        contracts=[contract],
        semantic_indexes=[semantic],
    )

    assert (
        "semantic_index_unmatched_interface:api_object:general-ledger/account"
        in report["gaps"]
    )


def test_semantic_index_missing_active_contract_is_observable() -> None:
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
    semantic_step1 = {
        **step1(),
        "changed_files": [{"path": semantic_path, "status": "modified"}],
    }

    report = resolve_candidates(
        semantic_step1,
        semantic_indexes=[semantic],
    )

    assert (
        "semantic_index_missing_active_contract:api_object:general-ledger/account"
        in report["gaps"]
    )


def test_source_anchor_joins_changed_symbol_to_exact_api_object() -> None:
    semantic = build_semantic_index_from_files(
        {
            "app/source/company/allocationentry.ent": "$kSchemas['allocationentry'] = array('module' => 'gl');",
            "app/source/openapispec/gl/models/objects.general-ledger.txn-allocation-template-line.s1.schema.yaml": "x-mappedTo: allocationentry\n",
            "app/source/company/AllocationManager.cls": (
                "<?php\nclass AllocationManager {\n"
                "  function ApplyCustomAllocation() {\n"
                "    $this->getManager('allocationentry');\n"
                "  }\n}\n"
            ),
        },
        repository="ia-app",
        revision=TARGET,
    )

    anchors = build_source_anchors(
        ["app/source/company/AllocationManager.cls"], [semantic]
    )

    assert len(anchors) == 1
    assert anchors[0]["source_symbol"] == "AllocationManager:ApplyCustomAllocation"
    assert anchors[0]["entity"] == "allocationentry"
    assert anchors[0]["interfaces"][0]["interface_id"] == (
        "api_object:general-ledger/txn-allocation-template-line"
    )


def test_likely_tests_are_ranked_from_source_evidence_and_inventory() -> None:
    anchors = [
        {
            "source_path": "app/source/company/AllocationManager.cls",
            "source_symbol": "AllocationManager:ApplyCustomAllocation",
            "source_lines": {"start": 4, "end": 4},
            "entity": "allocationentry",
            "source_revision": TARGET,
            "interfaces": [
                {
                    "interface_id": "api_object:general-ledger/txn-allocation-template-line",
                    "mapping_kind": "semantic_source_contract",
                    "source_revision": TARGET,
                    "evidence": [{"kind": "semantic_index"}],
                }
            ],
            "evidence": [{"source_path": "app/source/company/AllocationManager.cls"}],
        }
    ]
    candidate = {
        "target_repository": "intacct/ia-restapi-automation-tests",
        "interface_id": "repository:intacct/ia-restapi-automation-tests",
    }
    inventory = {
        "status": "available",
        "inventory_paths": [
            "features/fa/tc-transaction-allocation/transaction-allocation-MegaView.feature",
            "features/co/v1/location/location.feature",
            "features/ar/unrelated.feature",
        ],
    }

    ranked = rank_likely_tests(candidate, inventory, anchors, [])

    assert ranked
    assert ranked[0]["path"].endswith("transaction-allocation-MegaView.feature")
    assert ranked[0]["score"] > 0
    assert ranked[0]["score_rule_set_version"] == "0.1"
    assert all("evidence" in item and item["basis"] == "source_ranked" for item in ranked)


def provider_for(
    workflow_path: str,
    workflow_text: str,
    *,
    runs=None,
    artifacts=None,
    repository: str = "intacct/example",
):
    repo_prefix = f"repos/{repository}"
    responses = {
        repo_prefix: {
            "full_name": repository,
            "default_branch": "main",
        },
        f"{repo_prefix}/git/ref/heads/main": {"object": {"sha": DOWNSTREAM}},
        f"{repo_prefix}/git/trees/{DOWNSTREAM}?recursive=1": {
            "tree": [
                {"path": workflow_path, "type": "blob"},
                {"path": "features/example.feature", "type": "blob"},
                {"path": "testscripts/example.xml", "type": "blob"},
            ]
        },
        f"{repo_prefix}/contents/{workflow_path}?ref={DOWNSTREAM}": {
            "content": base64.b64encode(workflow_text.encode()).decode()
        },
        f"{repo_prefix}/actions/runs?head_sha={DOWNSTREAM}&per_page=100": {
            "workflow_runs": runs or []
        },
        f"{repo_prefix}/commits/{DOWNSTREAM}/check-runs?per_page=100": {
            "check_runs": []
        },
        f"{repo_prefix}/actions/artifacts?per_page=100": {"artifacts": artifacts or []},
    }
    for run in runs or []:
        responses[f"{repo_prefix}/actions/runs/{run['id']}/jobs?per_page=100"] = {
            "jobs": []
        }

    def provider(endpoint: str):
        if TARGET in endpoint:
            raise AssertionError(
                "source revision must not be used for downstream queries"
            )
        if endpoint not in responses:
            raise AssertionError(f"unexpected endpoint: {endpoint}")
        return responses[endpoint]

    return provider


def test_rest_inventory_without_linked_artifact_is_a_candidate(tmp_path: Path) -> None:
    repository = "intacct/ia-restapi-automation-tests"
    workflow = (
        Path(__file__).parent / "fixtures/greenfield/rest_workflow.yml"
    ).read_text()
    evidence = collect_repository_evidence(
        repository,
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider_for(
            ".github/workflows/rest.yml", workflow, repository=repository
        ),
    )
    report = resolve_candidates(step1(), inventory_evidence=[evidence])

    assert report["candidates"][0]["classification"] == "candidate"
    assert report["candidates"][0]["reason"] == "workflow_has_no_test_execution"
    assert f"ci_artifact_unavailable:{repository}" in report["gaps"]
    assert f"ci_linkage_unavailable:{repository}" in report["gaps"]
    assert evidence["ci_linkage"]["status"] == "unavailable"
    assert (
        evidence["workflows"][0]["classification"] == "test_preparation_with_artifact"
    )
    assert report["candidates"][0]["evidence"][0]["kind"] == "repository_inventory"
    assert validate(report) == []


def test_gateway_pass_only_workflow_never_proves_test_execution() -> None:
    repository = "intacct/ia-gwdata-gl"
    workflow = (
        Path(__file__).parent / "fixtures/greenfield/gateway_statuscheck.yml"
    ).read_text()
    evidence = collect_repository_evidence(
        repository,
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider_for(
            ".github/workflows/StatusCheck.yml", workflow, repository=repository
        ),
    )
    report = resolve_candidates(step1(), inventory_evidence=[evidence])

    candidate = report["candidates"][0]
    assert candidate["classification"] == "candidate"
    assert candidate["reason"] == "workflow_metadata_only"
    assert f"workflow_has_no_test_execution:{repository}" in report["gaps"]
    assert f"workflow_metadata_only:{repository}" in report["gaps"]


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
    runs = [{"id": 7, "head_sha": DOWNSTREAM, "name": "test"}]
    artifacts = [
        {
            "id": 8,
            "name": "step2-evidence",
            "workflow_run": {"id": 7},
            "source_repository": "ia-app",
            "source_revision": TARGET,
        },
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
    assert evidence["ci_linkage"]["status"] == "available"
    assert any(
        f"actions/runs?head_sha={DOWNSTREAM}" in endpoint
        for endpoint in evidence["provenance"]["endpoints"]
    )
    assert not any(
        TARGET in endpoint for endpoint in evidence["provenance"]["endpoints"]
    )


def test_unbound_artifact_does_not_prove_cross_repository_ci() -> None:
    workflow = "name: test\njobs:\n  test:\n    steps:\n      - run: mvn test\n"
    evidence = collect_repository_evidence(
        "intacct/ia-restapi-automation-tests",
        source_repository="ia-app",
        source_revision=TARGET,
        provider=provider_for(
            ".github/workflows/test.yml",
            workflow,
            runs=[{"id": 7, "head_sha": DOWNSTREAM}],
            artifacts=[{"id": 8, "workflow_run": {"id": 7}}],
            repository="intacct/ia-restapi-automation-tests",
        ),
    )

    report = resolve_candidates(step1(), inventory_evidence=[evidence])

    assert evidence["artifact_status"] == "not_linked_to_source_revision"
    assert evidence["ci_linkage"]["status"] == "unavailable"
    assert (
        "ci_linkage_unavailable:intacct/ia-restapi-automation-tests" in report["gaps"]
    )


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


def test_private_repository_failure_is_unavailable_in_step2() -> None:
    evidence = _unavailable_inventory(
        "intacct/ia-gwdata-gl",
        "ia-app",
        TARGET,
        "github_api_failed: private repository access denied",
    )

    report = resolve_candidates(step1(), inventory_evidence=[evidence])

    assert report["candidates"] == []
    assert "repository_access_unavailable:intacct/ia-gwdata-gl" in report["gaps"]
    assert validate(report) == []


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


def test_strict_ci_evidence_requires_execution_envelope(tmp_path: Path) -> None:
    path = tmp_path / "ci.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "evidence_id": "run-1",
                "repository": "intacct/tests",
                "commit_sha": CONSUMER,
                "source_repository": "ia-app",
                "source_revision": TARGET,
                "interface_id": "company.config.preference",
                "status": "available",
                "workflow_run_id": 10,
                "workflow_job_id": 11,
                "tests": [
                    {
                        "id": "test-one",
                        "path": "tests/test_one.py",
                        "execution_result": "passed",
                        "test_command": {
                            "argv": ["pytest", "tests/test_one.py"],
                            "cwd": ".",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    evidence = load_ci_evidence(path, strict=True)
    assert evidence["tests"][0]["test_command"]["argv"] == [
        "pytest",
        "tests/test_one.py",
    ]


def test_strict_ci_evidence_rejects_shell_command(tmp_path: Path) -> None:
    path = tmp_path / "ci.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "evidence_id": "run-1",
                "repository": "intacct/tests",
                "commit_sha": CONSUMER,
                "source_repository": "ia-app",
                "source_revision": TARGET,
                "interface_id": "company.config.preference",
                "status": "available",
                "workflow_run_id": 10,
                "workflow_job_id": 11,
                "tests": [
                    {
                        "id": "test-one",
                        "path": "tests/test_one.py",
                        "execution_result": "passed",
                        "test_command": "pytest tests/test_one.py",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError, match="structured argv"):
        load_ci_evidence(path, strict=True)


def test_strict_ci_evidence_requires_explicit_command_state(tmp_path: Path) -> None:
    path = tmp_path / "ci.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "evidence_id": "run-1",
                "repository": "intacct/tests",
                "commit_sha": CONSUMER,
                "source_repository": "ia-app",
                "source_revision": TARGET,
                "interface_id": "company.config.preference",
                "status": "available",
                "workflow_run_id": 10,
                "workflow_job_id": 11,
                "tests": [{
                    "id": "test-one",
                    "path": "tests/test_one.py",
                    "execution_result": "passed",
                }],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError, match="requires test_command"):
        load_ci_evidence(path, strict=True)


def test_target_evidence_is_revision_and_blob_pinned() -> None:
    revision = "0123456789abcdef0123456789abcdef01234567"
    content = b"Feature: Example\n"
    encoded = base64.b64encode(content).decode()
    endpoints = {
        f"repos/intacct/tests/git/trees/{revision}?recursive=1": {
            "tree": [
                {"path": "features/example.feature", "type": "blob", "sha": "f" * 40}
            ],
            "truncated": False,
        },
        "repos/intacct/tests/git/blobs/" + "f" * 40: {
            "encoding": "base64",
            "content": encoded,
        },
    }

    report = collect_target_evidence(
        "intacct/tests",
        revision=revision,
        paths=["features/example.feature"],
        provider=lambda endpoint: endpoints[endpoint],
    )
    assert report["provider"] == "github_git_api"
    assert report["revision"] == revision
    assert report["files"][0]["blob_or_response_id"] == "f" * 40
    assert len(report["evidence_sha256"]) == 64


def test_target_evidence_rejects_synthetic_revision() -> None:
    with pytest.raises(RepositoryEvidenceError, match="must not be synthetic"):
        collect_target_evidence(
            "intacct/tests",
            revision="d" * 40,
            paths=["features/example.feature"],
            provider=lambda _: {},
        )
