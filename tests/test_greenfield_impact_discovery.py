import subprocess
from pathlib import Path

from greenfield.impact_discovery import materialize_local_reads, validate_read_requests


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_discovery_reads_are_revision_and_path_bounded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "source.txt").write_text("exact", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    revision = _git(root, "rev-parse", "HEAD")
    requests = [{"repository": "intacct/ia-app", "revision": revision, "path": "source.txt"}]
    assert validate_read_requests(requests, allowed_repository="intacct/ia-app", allowed_revision=revision) == []
    assert materialize_local_reads(source_root=root, requests=requests) == [{"path": "source.txt", "status": "available", "content": "exact"}]
    assert validate_read_requests([{**requests[0], "path": "../secret"}], allowed_repository="intacct/ia-app", allowed_revision=revision)
