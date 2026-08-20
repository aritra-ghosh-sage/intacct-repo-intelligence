from __future__ import annotations

import json
from pathlib import Path

from scripts import replay_greenfield_step1_5

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
