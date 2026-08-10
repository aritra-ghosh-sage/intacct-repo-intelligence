from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import catalog.github_pr_metadata as metadata
from catalog.github_pr_metadata import GitHubPrMetadataError, fetch_pr_metadata, normalize_pr_metadata


def _manifest(tmp_path: Path) -> Path:
    root = tmp_path / "main"
    root.mkdir()
    path = tmp_path / "manifest.yaml"
    path.write_text(
        """version: 1
repositories:
  - repo_key: ia-main
    remote_url: git@github.com:intacct/ia-app.git
    local_root: %s
    tracked_branch: main
""" % root,
        encoding="utf-8",
    )
    return path


def _pull_request() -> dict:
    return {
        "number": 49156,
        "html_url": "https://github.com/intacct/ia-app/pull/49156",
        "title": "Example",
        "base": {"sha": "b" * 40, "ref": "main"},
        "head": {"sha": "a" * 40, "ref": "feature"},
        "labels": [{"name": "risk"}],
    }


def test_metadata_normalizes_pr_identity_and_revisions() -> None:
    value = normalize_pr_metadata(
        repository="intacct/ia-app",
        repo_key="ia-main",
        pull_request=_pull_request(),
        files=[{"filename": "app/a.php", "status": "modified"}],
        reviews=[{"id": 1, "state": "approved", "body": "ok"}],
        inline_comments=[],
        issue_comments=[],
        check_runs=[{"id": 2, "name": "tests", "status": "completed"}],
        provider="gh_api",
        endpoints=["repos/intacct/ia-app/pulls/49156"],
    )
    assert value["repository"] == "intacct/ia-app"
    assert value["pull_request"]["target_revision"] == "a" * 40
    assert value["reviews"][0]["id"] == 1
    assert value["provenance"]["provider"] == "gh_api"


def test_metadata_rejects_missing_required_fields() -> None:
    pull = _pull_request()
    del pull["head"]
    with pytest.raises(GitHubPrMetadataError):
        normalize_pr_metadata(
            repository="intacct/ia-app",
            repo_key="ia-main",
            pull_request=pull,
            files=[], reviews=[], inline_comments=[], issue_comments=[], check_runs=[],
            provider="gh_api", endpoints=[],
        )


def test_fetch_metadata_normalizes_provider_collections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter([
        _pull_request(),
        [{"filename": "app/a.php", "status": "modified"}],
        [{"id": 1, "state": "approved"}],
        [{"id": 2, "path": "app/a.php"}],
        [{"id": 3, "body": "comment"}],
        [{"id": 4, "name": "tests"}, {"id": 5, "name": "lint"}],
    ])
    calls = []
    def provider(endpoint, collection, collection_key=None):
        calls.append((endpoint, collection, collection_key))
        return next(values), "gh_api"
    monkeypatch.setattr(metadata, "_provider_call", provider)
    value = fetch_pr_metadata(repo_key="ia-main", manifest_path=_manifest(tmp_path), pr_number=49156)
    assert value["pull_request"]["number"] == 49156
    assert len(value["changed_files"]) == 1
    assert len(value["check_runs"]) == 2
    assert calls[-1][1:] == (True, "check_runs")


def test_gh_check_run_pages_are_flattened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata.shutil, "which", lambda name: "/usr/bin/gh")
    result = SimpleNamespace(
        returncode=0,
        stdout=json.dumps([
            {"check_runs": [{"id": 1}]},
            {"check_runs": [{"id": 2}]},
        ]).encode(),
        stderr=b"",
    )
    monkeypatch.setattr(metadata.subprocess, "run", lambda *args, **kwargs: result)
    assert metadata._gh_json("repos/intacct/ia-app/commits/a/check-runs", collection=True, collection_key="check_runs") == [{"id": 1}, {"id": 2}]


def test_http_check_run_pages_follow_next_link(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __init__(self, payload, link=""):
            self.payload = payload
            self.headers = {"Link": link}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(self.payload).encode()

    responses = iter([
        Response({"check_runs": [{"id": 1}]}, '<https://api.github.com/repos/intacct/ia-app/commits/a/check-runs?page=2>; rel="next"'),
        Response({"check_runs": [{"id": 2}]}),
    ])
    monkeypatch.setattr(metadata, "urlopen", lambda request: next(responses))
    assert metadata._http_json("repos/intacct/ia-app/commits/a/check-runs", token="token", collection=True, collection_key="check_runs") == [{"id": 1}, {"id": 2}]


def test_provider_failure_does_not_return_partial_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata.shutil, "which", lambda name: None)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubPrMetadataError):
        metadata._provider_call("repos/intacct/ia-app/pulls/1", collection=False)
