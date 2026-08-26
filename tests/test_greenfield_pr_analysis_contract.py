from greenfield.pr_analysis_contract import make_request, validate_claims


def test_request_is_bound_to_exact_step1_revisions() -> None:
    step1 = {"input": {"repository": "intacct/ia-app", "repo_key": "ia-main", "pr_number": 49695, "base_sha": "a" * 40, "target_revision": "b" * 40}, "changed_files": [{"path": "app/source/setup/create_tmplrepprd.inc"}]}
    request = make_request(step1)
    assert request["head_revision"] == "b" * 40
    assert request["changed_paths"] == ["app/source/setup/create_tmplrepprd.inc"]


def test_claim_requires_revision_and_evidence() -> None:
    report = {"schema_version": "0.1", "analysis_kind": "greenfield_impact_discovery", "status": "partial", "claims": [{"repository": "intacct/ia-app", "inspected_revision": "b" * 40, "evidence_status": "confirmed", "evidence": [{"artifact": "x"}], "rationale": "exact trace"}]}
    assert validate_claims(report, kind="greenfield_impact_discovery") == []
    report["claims"][0]["evidence_status"] = "guessed"
    assert validate_claims(report, kind="greenfield_impact_discovery")
