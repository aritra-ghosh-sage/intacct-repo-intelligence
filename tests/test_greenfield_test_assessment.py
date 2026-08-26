from greenfield.test_assessment import build_assessment, validate_assessment


def test_unassessed_repository_cannot_become_test_proposal() -> None:
    report = build_assessment(repository="intacct/tests", revision="a" * 40, candidates=[], evidence=[], assessed=False)
    assert report["assessments"] == []
    assert report["gaps"] == ["test_repository_not_assessed"]
    assert validate_assessment(report) == []
