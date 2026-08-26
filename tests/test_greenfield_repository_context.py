import subprocess
from pathlib import Path

from greenfield.repository_context import collect_repository_context


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_context_uses_revision_bound_codeowners_without_inventing_owner(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "CODEOWNERS").write_text("/app/db/ @db-team\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("advisory", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    revision = _git(root, "rev-parse", "HEAD")
    context = collect_repository_context(repository="example/repo", revision=revision, changed_paths=["app/db/a.sql", "app/source/a.inc"], local_root=root)
    assert context["owners"][0]["owners"] == ["@db-team"]
    assert context["owners"][1]["status"] == "ownership_unavailable"
    assert context["guidance"][0]["path"] == "AGENTS.md"


def test_github_fallback_keeps_optional_files_and_codeowners(
    monkeypatch,
) -> None:
    from greenfield import repository_context

    def fake_target(repository, *, revision, paths, provider, include_content, allow_missing):
        assert paths == sorted(paths)
        assert include_content is True
        assert allow_missing is True
        return {"files": [{"path": "CODEOWNERS", "content_sha256": "a" * 64, "content": "/app/db/ @db-team\n"}]}

    monkeypatch.setattr(repository_context, "collect_target_evidence", fake_target)
    context = repository_context.collect_repository_context(
        repository="intacct/tests",
        revision="b" * 40,
        changed_paths=["app/db/a.sql"],
    )
    assert context["owners"][0]["owners"] == ["@db-team"]
