from greenfield.pr_review import render_review, validate_review


def test_review_keeps_canonical_template_and_explicit_gap() -> None:
    request = {"source_repository": "intacct/ia-app", "base_revision": "a" * 40, "head_revision": "b" * 40}
    discovery = {"claims": [{"repository": "intacct/ia-app", "inspected_revision": "b" * 40, "evidence_status": "confirmed", "evidence": [{"artifact": "trace"}], "rationale": "exact"}], "gaps": ["cross_repository_discovery_requires_confirmed_relation_or_bound_ci_evidence"]}
    assessment = {"gaps": ["test_repository_not_assessed"]}
    review = render_review(request=request, discovery=discovery, assessment=assessment, ci_evidence=[], contexts=[])
    assert "## 🧪 Test Coverage & Obligations" in review["markdown"]
    assert "test_repository_not_assessed" in review["markdown"]
    assert validate_review(review) == []
    assert "path/to/file1.cls" not in review["markdown"]
    assert "[1-2 sentence description]" not in review["markdown"]
    assert "Required evidence to continue" in review["markdown"]


def test_review_retains_validated_analysis_and_planning_provenance() -> None:
    request = {"source_repository": "intacct/ia-app", "base_revision": "a" * 40, "head_revision": "b" * 40}
    discovery = {"claims": [], "gaps": []}
    review = render_review(
        request=request,
        discovery=discovery,
        assessment={"gaps": []},
        ci_evidence=[],
        contexts=[],
        analysis={"report_sha256": "c" * 64, "repository_impacts": [{"repository": "intacct/tests", "evidence_state": "candidate"}], "coverage": {"status": "candidate"}, "gaps": ["coverage_unbound"]},
        behavior_impact={"handbook_sha256": "d" * 64},
        planning={"planning_sha256": "e" * 64, "status": "complete", "cycles": [{}], "gaps": []},
    )
    assert "Ranked impact" in review["markdown"]
    assert "Planner lifecycle" in review["markdown"]
    assert review["provenance"]["analysis_report_sha256"] == "c" * 64
