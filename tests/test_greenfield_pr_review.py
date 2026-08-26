from greenfield.pr_review import render_review, validate_review


def test_review_keeps_canonical_template_and_explicit_gap() -> None:
    request = {"source_repository": "intacct/ia-app", "base_revision": "a" * 40, "head_revision": "b" * 40}
    discovery = {"claims": [{"repository": "intacct/ia-app", "inspected_revision": "b" * 40, "evidence_status": "confirmed", "evidence": [{"artifact": "trace"}], "rationale": "exact"}], "gaps": ["cross_repository_discovery_requires_confirmed_relation_or_bound_ci_evidence"]}
    assessment = {"gaps": ["test_repository_not_assessed"]}
    review = render_review(request=request, discovery=discovery, assessment=assessment, ci_evidence=[], contexts=[])
    assert "## 🧪 Test Coverage & Obligations" in review["markdown"]
    assert "test_repository_not_assessed" in review["markdown"]
    assert validate_review(review) == []
