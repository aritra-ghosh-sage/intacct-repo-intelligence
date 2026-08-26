from greenfield.github_repository_evidence import classify_ci_execution


def test_pending_review_jenkins_is_control_only_and_not_execution() -> None:
    report = classify_ci_execution(workflow_runs=[{"id": 1}], workflow_jobs=[{"name": "MigrationCheck_JenkinsjobTrigger", "conclusion": "pending reviews"}, {"name": "Jenkinsbuild_Migration", "conclusion": "skipped"}])
    assert report["execution_status"] == "workflow_control_only"
    assert report["test_job_count"] == 0
