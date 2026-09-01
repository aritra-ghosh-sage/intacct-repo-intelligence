from greenfield.test_assessment import build_assessment, validate_assessment


def test_unassessed_repository_cannot_become_test_proposal() -> None:
    report = build_assessment(
        repository="intacct/tests",
        revision="a" * 40,
        candidates=[],
        evidence=[],
        assessed=False,
        analysis_report_sha256="b" * 64,
        canonical_analysis={
            "analysis_report_sha256": "b" * 64,
            "repository_impacts": [],
            "coverage": {},
            "actions": [],
            "gaps": [],
        },
    )
    assert report["assessments"] == []
    assert report["gaps"] == ["test_repository_not_assessed"]
    assert validate_assessment(report) == []
