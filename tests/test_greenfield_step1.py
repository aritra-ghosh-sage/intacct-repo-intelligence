from __future__ import annotations

import json
from pathlib import Path

import pytest

from greenfield.step1_capture import (
    ANALYSIS_KIND,
    CaptureError,
    build_report,
    capture_pr,
    evidence_fingerprint,
)
from scripts import trace_greenfield_step1
from scripts.validate_greenfield_step1 import validate

BASE = "b" * 40
HEAD = "a" * 40


def _metadata() -> dict:
    return {
        "schema_version": "0.2",
        "analysis_kind": "pr_impact_metadata",
        "repo_key": "ia-main",
        "repository": "intacct/ia-app",
        "pull_request": {
            "number": 49156,
            "url": "https://github.com/intacct/ia-app/pull/49156",
            "title": "Example",
            "base_revision": BASE,
            "target_revision": HEAD,
        },
        "changed_files": [
            {"filename": "app/z.php", "status": "modified"},
            {"filename": "app/a.php", "status": "added"},
        ],
        "linked_issues": [
            {
                "repository": "intacct/other",
                "number": 42,
                "relation": "cross_referenced",
            }
        ],
        "workflow_runs": [
            {"id": 10, "name": "tests", "workflow_id": 3, "head_sha": HEAD}
        ],
        "workflow_jobs": [{"id": 11, "name": "unit", "workflow_run_id": 10}],
        "check_runs": [
            {"id": 12, "name": "checks", "status": "completed", "head_sha": HEAD}
        ],
        "evidence_status": {
            "linked_issues": "available",
            "workflow_runs": "available",
            "workflow_jobs": "available",
            "check_runs": "available",
        },
        "provenance": {
            "provider": "fixture",
            "endpoints": ["pull"],
            "fetched_at": "2026-08-20T00:00:00Z",
        },
    }


def test_build_report_exposes_step2_input_and_sorts_files() -> None:
    report = build_report(_metadata())

    assert report["analysis_kind"] == ANALYSIS_KIND
    assert report["status"] == "complete"
    assert report["input"] == {
        "repository": "intacct/ia-app",
        "repo_key": "ia-main",
        "pr_number": 49156,
        "base_sha": BASE,
        "head_sha": HEAD,
        "base_revision": BASE,
        "target_revision": HEAD,
        "changed_paths": ["app/a.php", "app/z.php"],
    }
    assert [row["path"] for row in report["changed_files"]] == [
        "app/a.php",
        "app/z.php",
    ]
    assert report["provenance"]["evidence_sha256"] == evidence_fingerprint(report)
    assert validate(report) == []


def test_fingerprint_is_stable_when_fetch_time_changes() -> None:
    first = build_report(_metadata())
    second_metadata = _metadata()
    second_metadata["provenance"]["fetched_at"] = "2026-08-21T00:00:00Z"
    second = build_report(second_metadata)

    assert (
        first["provenance"]["evidence_sha256"]
        == second["provenance"]["evidence_sha256"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("base_revision", "bad"), ("target_revision", "bad"), ("changed_files", [])],
)
def test_capture_rejects_missing_or_invalid_required_evidence(
    field: str, value: object
) -> None:
    metadata = _metadata()
    if field in {"base_revision", "target_revision"}:
        metadata["pull_request"][field] = value
    else:
        metadata[field] = value
    with pytest.raises(CaptureError):
        build_report(metadata)


def test_capture_rejects_mismatched_workflow_and_check_sha() -> None:
    metadata = _metadata()
    metadata["workflow_runs"][0]["head_sha"] = BASE
    with pytest.raises(CaptureError, match="workflow run"):
        build_report(metadata)

    metadata = _metadata()
    metadata["check_runs"][0]["head_sha"] = BASE
    with pytest.raises(CaptureError, match="check run"):
        build_report(metadata)


def test_capture_rejects_orphan_workflow_job() -> None:
    metadata = _metadata()
    metadata["workflow_jobs"][0]["workflow_run_id"] = 999
    with pytest.raises(CaptureError, match="missing workflow run"):
        build_report(metadata)


def test_unavailable_optional_evidence_is_partial_and_explicit() -> None:
    metadata = _metadata()
    metadata["workflow_runs"] = []
    metadata["workflow_jobs"] = []
    metadata["evidence_status"]["workflow_runs"] = "unavailable"
    metadata["evidence_status"]["workflow_jobs"] = "unavailable"

    report = build_report(metadata)

    assert report["status"] == "partial"
    assert "workflow_runs:unavailable" in report["gaps"]
    assert validate(report) == []


def test_step2_accepts_the_greenfield_step1_shape() -> None:
    report = build_report(_metadata())
    assert report["input"]["target_revision"] == HEAD
    assert report["changed_files"][0]["path"] == "app/a.php"


def test_validator_rejects_tampered_fingerprint() -> None:
    report = build_report(_metadata())
    report["changed_files"][0]["status"] = "deleted"
    assert any("evidence_sha256" in error for error in validate(report))


def test_blocked_cli_writes_atomic_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "nested" / "step1.json"

    def fail_capture(**_: object) -> dict:
        raise CaptureError("provider unavailable")

    monkeypatch.setattr(trace_greenfield_step1, "capture_pr", fail_capture)
    assert (
        trace_greenfield_step1.main(
            ["--repo-key", "ia-main", "--pr", "1", "--output", str(output)]
        )
        == 2
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert validate(report) == []


@pytest.mark.parametrize(
    ("message", "code"),
    [
        (
            "no GitHub provider is available; gh error: gh api failed: error connecting to api.github.com",
            "provider_unavailable",
        ),
        (
            "repo_not_found: manifest must contain exactly one 'ia-app' entry",
            "manifest_identity_mismatch",
        ),
    ],
)
def test_blocked_cli_classifies_capture_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    code: str,
) -> None:
    output = tmp_path / "step1.json"

    def fail_capture(**_: object) -> dict:
        raise CaptureError(message)

    monkeypatch.setattr(trace_greenfield_step1, "capture_pr", fail_capture)
    assert (
        trace_greenfield_step1.main(
            ["--repo-key", "ia-main", "--pr", "1", "--output", str(output)]
        )
        == 2
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["error"]["code"] == code
    assert validate(report) == []


def test_cli_reports_output_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        trace_greenfield_step1, "capture_pr", lambda **_: build_report(_metadata())
    )

    def fail_write(*_: object, **__: object) -> None:
        raise OSError("output is not writable")

    monkeypatch.setattr(trace_greenfield_step1, "_write_atomic", fail_write)
    assert (
        trace_greenfield_step1.main(
            [
                "--repo-key",
                "ia-main",
                "--pr",
                "49156",
                "--output",
                str(tmp_path / "step1.json"),
            ]
        )
        == 2
    )
    assert "greenfield_step1_failed: output is not writable" in capsys.readouterr().err


def test_capture_pr_uses_existing_read_only_metadata_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "greenfield.step1_capture.fetch_pr_metadata",
        lambda **_: _metadata(),
    )
    report = capture_pr(
        repo_key="ia-main", manifest_path="manifest.yaml", pr_number=49156
    )
    assert report["input"]["repository"] == "intacct/ia-app"
