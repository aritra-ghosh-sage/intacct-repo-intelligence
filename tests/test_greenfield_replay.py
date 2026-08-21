from __future__ import annotations

import json
from pathlib import Path

from scripts import replay_greenfield_step1_5
from scripts import (
    trace_greenfield_step2,
    trace_greenfield_step3,
    trace_greenfield_step4,
    trace_greenfield_step5,
)
from greenfield.step4_contract import validate_step4_report
from greenfield.step5_actions import validate_step5_report

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "examples" / "greenfield" / "ia-app-pr-49156" / "replay"


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


def test_optional_related_evidence_uses_stable_repository_path() -> None:
    absolute_bundle = BUNDLE.resolve()

    assert replay_greenfield_step1_5._evidence_path(
        absolute_bundle, "step3.related-pr-evidence.json"
    ) == Path("examples/greenfield/ia-app-pr-49156/replay/step3.related-pr-evidence.json")


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

    assert trace_greenfield_step2.main(
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
    ) == 0
    assert trace_greenfield_step3.main(
        ["--step2-report", str(step2), "--output", str(step3)]
    ) == 0
    assert trace_greenfield_step4.main(
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
    ) == 0
    assert trace_greenfield_step5.main(
        [
            "--step3-report",
            str(step3),
            "--step4-report",
            str(step4),
            "--output",
            str(step5),
        ]
    ) == 0

    assert validate_step4_report(json.loads(step4.read_text(encoding="utf-8"))) == []
    assert validate_step5_report(json.loads(step5.read_text(encoding="utf-8"))) == []

    tampered_step3 = json.loads(step3.read_text(encoding="utf-8"))
    tampered_step3["input"]["target_revision"] = "0" * 40
    tampered_path = tmp_path / "tampered-step3.json"
    tampered_path.write_text(json.dumps(tampered_step3), encoding="utf-8")
    assert trace_greenfield_step5.main(
        [
            "--step3-report",
            str(tampered_path),
            "--step4-report",
            str(step4),
            "--output",
            str(tmp_path / "tampered-step5.json"),
        ]
    ) == 2
