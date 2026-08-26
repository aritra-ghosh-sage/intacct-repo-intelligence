from __future__ import annotations

import json
import shutil
from pathlib import Path

from greenfield.replay_validation import validation_summary
from greenfield.step4_contract import validate_step4_report
from greenfield.step5_actions import validate_step5_report
from scripts import (
    replay_greenfield_step1_5,
    replay_greenfield_step1_6,
    run_greenfield_codex,
    trace_greenfield_step2,
    trace_greenfield_step3,
    trace_greenfield_step4,
    trace_greenfield_step5,
)

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "examples" / "greenfield" / "ia-app-pr-49156" / "replay"
PR_49137_BUNDLE = ROOT / "examples" / "greenfield" / "ia-app-pr-49137" / "replay"


def test_pr_49137_replay_is_golden_and_keeps_unproven_surfaces_explicit(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "greenfield-pr-49137"
    assert (
        replay_greenfield_step1_6.main(
            ["--bundle-dir", str(PR_49137_BUNDLE), "--output-dir", str(output_dir)]
        )
        == 0
    )
    step3 = json.loads((output_dir / "step3.report.json").read_text(encoding="utf-8"))
    repositories = {
        item["repository"]: item["classification"]
        for item in step3["potentially_affected_repositories"]["items"]
    }
    assert repositories["ia-app"] == "confirmed"
    assert repositories["intacct/ia-gwdata-gl"] == "candidate"
    assert repositories["intacct/ia-rest-api-testing"] == "candidate"
    assert repositories["intacct/ia-test-automation"] == "unresolved"
    assert step3["surface_statuses"]["xml_api"]["status"] == "not_run"
    assert step3["surface_statuses"]["csv_import"]["status"] == "not_run"
    assert step3["surface_statuses"]["cross_module_callers"]["status"] == "unresolved"
    assert not (output_dir / "step6.report.json").exists()


def test_replay_bundle_reproduces_golden_reports(tmp_path: Path) -> None:
    output_dir = tmp_path / "greenfield-replay"

    assert (
        replay_greenfield_step1_5.main(
            ["--bundle-dir", str(BUNDLE), "--output-dir", str(output_dir)]
        )
        == 0
    )

    for name in (
        "step2.report.json",
        "step3.report.json",
        "step4.report.json",
        "step5.report.json",
    ):
        expected = json.loads((BUNDLE / name).read_text(encoding="utf-8"))
        actual = json.loads((output_dir / name).read_text(encoding="utf-8"))
        assert actual == expected


def test_replay_bundle_reproduces_step6_golden_reports(tmp_path: Path) -> None:
    output_dir = tmp_path / "greenfield-replay-step6"
    assert (
        replay_greenfield_step1_6.main(
            ["--bundle-dir", str(BUNDLE), "--output-dir", str(output_dir)]
        )
        == 0
    )
    for name in (
        "step2.report.json",
        "step3.report.json",
        "step4.report.json",
        "step5.report.json",
        "step6.report.json",
    ):
        expected = json.loads((BUNDLE / name).read_text(encoding="utf-8"))
        actual = json.loads((output_dir / name).read_text(encoding="utf-8"))
        assert actual == expected


def test_step6_replay_resolves_bundle_files_from_any_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    output_dir = tmp_path / "greenfield-replay-cwd"
    monkeypatch.chdir(tmp_path)
    assert (
        replay_greenfield_step1_6.main(
            ["--bundle-dir", str(BUNDLE.resolve()), "--output-dir", str(output_dir)]
        )
        == 0
    )


def test_optional_related_evidence_uses_stable_repository_path() -> None:
    absolute_bundle = BUNDLE.resolve()

    assert replay_greenfield_step1_5._evidence_path(
        absolute_bundle, "step3.related-pr-evidence.json"
    ) == Path(
        "examples/greenfield/ia-app-pr-49156/replay/step3.related-pr-evidence.json"
    )


def test_retained_step1_5_artifacts_are_consumed_directly(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(PR_49137_BUNDLE, bundle)
    (bundle / "step2.contract.yaml").unlink()
    step1 = json.loads((bundle / "step1.json").read_text(encoding="utf-8"))
    contract, name = replay_greenfield_step1_6._load_contract_for_replay(bundle, step1)
    assert name == "step1.5.contract.json"
    assert contract["revision"] == step1["input"]["head_sha"]


def test_pr_49156_replays_both_step1_5_and_declared_contracts() -> None:
    step1 = json.loads((BUNDLE / "step1.json").read_text(encoding="utf-8"))
    contracts, names, _retained = replay_greenfield_step1_5._load_contracts(
        BUNDLE, step1
    )
    assert names == ["step1.5.contract.json", "step2.contract.yaml"]
    assert len(contracts) == 2
    assert {
        contract["relationship_type"] for contract in contracts[0]["relations"]
    } == {"behavior_contract"}
    assert {
        contract["relationship_type"] for contract in contracts[1]["relations"]
    } == {"api_contract"}


def test_contract_deduplication_keeps_distinct_interfaces() -> None:
    generated = {
        "relations": [
            {
                "interface_id": "generated.interface",
                "consumer_repository": "consumer",
                "relationship_type": "api_contract",
                "source_paths": ["app/source/a.php"],
            }
        ]
    }
    declared = {
        "relations": [
            {
                "interface_id": "declared.interface",
                "consumer_repository": "consumer",
                "relationship_type": "api_contract",
                "source_paths": ["app/source/a.php"],
            }
        ]
    }
    assert not replay_greenfield_step1_5._is_redundant_contract(generated, declared)


def test_step7_replay_blocks_without_strict_step6_evidence(tmp_path: Path) -> None:
    output_dir = tmp_path / "replay"
    assert (
        replay_greenfield_step1_6.main(
            ["--bundle-dir", str(BUNDLE), "--output-dir", str(output_dir)]
        )
        == 0
    )
    handoff = json.loads(
        (output_dir / "step7.handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["pr_eligible"] is False
    assert handoff["reason"] == "step6_strict_evidence_unavailable"
    assert handoff["status"] == "blocked"
    assert handoff["details"]


def test_step7_replay_blocks_when_request_has_no_strict_step6_inputs(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    (bundle / "step6.request.json").unlink()
    output_dir = tmp_path / "output"
    assert (
        replay_greenfield_step1_6.main(
            ["--bundle-dir", str(bundle), "--output-dir", str(output_dir)]
        )
        == 2
    )
    handoff = json.loads(
        (output_dir / "step7.handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["status"] == "blocked"
    assert handoff["reason"] == "step6_report_not_supplied"


def test_validation_summary_does_not_upgrade_structure_to_completeness() -> None:
    summary = validation_summary(
        artifact_integrity="passed",
        provenance_revision_consistency="passed",
        step3={
            "status": "complete",
            "gaps": [],
            "surface_statuses": {"runtime": {"status": "unavailable"}},
        },
        step4={
            "status": "complete",
            "gaps": [],
            "coverage": {"status": "available", "items": []},
            "obligations": {"status": "available", "items": []},
        },
    )
    assert summary["artifact_integrity"]["status"] == "passed"
    assert summary["provenance_revision_consistency"]["status"] == "passed"
    assert summary["impact_completeness"]["status"] == "partial"
    assert summary["coverage_completeness"]["status"] == "partial"
    assert summary["runtime_validation"]["status"] == "unavailable"
    assert summary["pr_eligibility"]["pr_eligible"] is False


def test_validation_summary_requires_successful_executed_coverage() -> None:
    def summary_for(result: str | None) -> dict:
        item = {
            "classification": "covered",
            "evidence": [{"kind": "ci"}],
        }
        if result is not None:
            item["execution_result"] = result
        return validation_summary(
            artifact_integrity="passed",
            provenance_revision_consistency="passed",
            step3={"status": "complete", "gaps": []},
            step4={
                "status": "complete",
                "gaps": [],
                "coverage": {"status": "available", "items": [item]},
                "obligations": {
                    "status": "available",
                    "items": [{"status": "satisfied"}],
                },
            },
        )

    assert summary_for("passed")["coverage_completeness"]["status"] == "complete"
    assert summary_for("failed")["coverage_completeness"]["status"] == "partial"
    assert summary_for(None)["coverage_completeness"]["status"] == "partial"


def test_validation_summary_checks_impact_classification_states() -> None:
    summary = validation_summary(
        artifact_integrity="passed",
        provenance_revision_consistency="passed",
        step3={
            "status": "complete",
            "gaps": [],
            "impact": {"items": [{"classification": "unresolved"}]},
        },
        step4=None,
    )
    assert summary["impact_completeness"]["status"] == "partial"


def test_runner_step6_handoff_does_not_infer_target_inputs() -> None:
    handoff = run_greenfield_codex._step6_handoff()
    assert handoff["status"] == "unavailable"
    assert handoff["invoked"] is False
    assert handoff["pr_eligible"] is False
    assert handoff["required_inputs"]
    assert "target repository" in handoff["required_inputs"][1]


def test_runner_exposes_wrapper_handoff_artifact() -> None:
    source = Path(run_greenfield_codex.__file__).read_text(encoding="utf-8")
    assert "GreenfieldFlowHandoff" in source
    assert '"flow_handoff": handoff.path' in source


def test_step1_5_trace_and_contract_must_be_paired(tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(PR_49137_BUNDLE, bundle)
    (bundle / "step1.5.contract.json").unlink()
    assert replay_greenfield_step1_6.main(["--bundle-dir", str(bundle)]) == 2
    assert (
        "both step1.5.trace.json and step1.5.contract.json" in capsys.readouterr().err
    )


def test_stale_retained_step1_5_contract_fails_closed(tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(PR_49137_BUNDLE, bundle)
    contract = json.loads(
        (bundle / "step1.5.contract.json").read_text(encoding="utf-8")
    )
    contract["revision"] = "0" * 40
    (bundle / "step1.5.contract.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )
    assert replay_greenfield_step1_6.main(["--bundle-dir", str(bundle)]) == 2
    assert "contract" in capsys.readouterr().err


def test_step1_5_contract_trace_fingerprint_must_match(tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(PR_49137_BUNDLE, bundle)
    contract = json.loads(
        (bundle / "step1.5.contract.json").read_text(encoding="utf-8")
    )
    contract["generation"]["source_trace_sha256"] = "0" * 64
    (bundle / "step1.5.contract.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )
    assert replay_greenfield_step1_6.main(["--bundle-dir", str(bundle)]) == 2
    assert "trace fingerprint" in capsys.readouterr().err


def test_standardized_replay_does_not_use_ambient_generated_contract(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    step1 = json.loads((bundle / "step1.json").read_text(encoding="utf-8"))
    _contract, name = replay_greenfield_step1_6._load_contract_for_replay(bundle, step1)
    assert name == "step1.5.contract.json"


def test_retained_step1_5_artifacts_are_byte_stable() -> None:
    for name in ("step1.5.trace.json", "step1.5.contract.json"):
        first = (PR_49137_BUNDLE / name).read_bytes()
        second = (PR_49137_BUNDLE / name).read_bytes()
        assert first == second


def test_cli_chain_reaches_step5_and_validates_intermediate_reports(
    tmp_path: Path,
) -> None:
    bundle = BUNDLE
    step1 = bundle / "step1.json"
    contract = bundle / "step2.contract.yaml"
    ci = bundle / "step2.ci.json"
    inventory = bundle / "step2.inventory.json"
    step2 = tmp_path / "step2.json"
    step3 = tmp_path / "step3.json"
    step4 = tmp_path / "step4.json"
    step5 = tmp_path / "step5.json"

    assert (
        trace_greenfield_step2.main(
            [
                "--step1-report",
                str(step1),
                "--contract",
                str(contract),
                "--ci-evidence",
                str(ci),
                "--inventory-evidence",
                str(inventory),
                "--output",
                str(step2),
            ]
        )
        == 0
    )
    assert (
        trace_greenfield_step3.main(
            ["--step2-report", str(step2), "--output", str(step3)]
        )
        == 0
    )
    assert (
        trace_greenfield_step4.main(
            [
                "--step3-report",
                str(step3),
                "--contract",
                str(contract),
                "--ci-evidence",
                str(ci),
                "--inventory",
                str(inventory),
                "--output",
                str(step4),
            ]
        )
        == 0
    )
    assert (
        trace_greenfield_step5.main(
            [
                "--step3-report",
                str(step3),
                "--step4-report",
                str(step4),
                "--output",
                str(step5),
            ]
        )
        == 0
    )

    assert validate_step4_report(json.loads(step4.read_text(encoding="utf-8"))) == []
    assert validate_step5_report(json.loads(step5.read_text(encoding="utf-8"))) == []

    tampered_step3 = json.loads(step3.read_text(encoding="utf-8"))
    tampered_step3["input"]["target_revision"] = "0" * 40
    tampered_path = tmp_path / "tampered-step3.json"
    tampered_path.write_text(json.dumps(tampered_step3), encoding="utf-8")
    assert (
        trace_greenfield_step5.main(
            [
                "--step3-report",
                str(tampered_path),
                "--step4-report",
                str(step4),
                "--output",
                str(tmp_path / "tampered-step5.json"),
            ]
        )
        == 2
    )
