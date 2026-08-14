from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from catalog import pr_review_catalog
from catalog.refresh_transaction import CatalogPromotionError

BASE = "a" * 40
TARGET = "b" * 40


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _metadata() -> dict:
    return {
        "repository": "intacct/ia-app",
        "pull_request": {
            "number": 48480,
            "base_revision": BASE,
            "target_revision": TARGET,
        },
    }


def _catalog(path: Path, target: str = TARGET) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            Path("catalog/repo_v1_schema.sql").read_text(encoding="utf-8")
        )
        build_id = conn.execute(
            "INSERT INTO catalog_builds(build_token,catalog_path,status,source_revisions_json) VALUES(?,?,?,?)",
            ("build", str(path), "active", json.dumps({"ia-main": target})),
        ).lastrowid
        conn.execute(
            "INSERT INTO repos(repo_key,local_root,tracked_branch,target_commit_sha,build_id) VALUES(?,?,?,?,?)",
            ("ia-main", "/source", "main", target, build_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_verify_catalog_rejects_non_exact_revision(tmp_path: Path) -> None:
    db = tmp_path / "catalog.db"
    _catalog(db, BASE)

    with pytest.raises(pr_review_catalog.PrReviewCatalogError) as caught:
        pr_review_catalog.verify_catalog(db, repo_key="ia-main", target_sha=TARGET)

    assert caught.value.code == "catalog_revision_mismatch"
    assert "exact PR head SHA" in caught.value.fix


def test_run_git_converts_timeout_to_stable_catalog_error(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, object]] = []

    class TimeoutProcess:
        pid = 12345
        returncode = None

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(["git"], timeout)
            self.returncode = -9
            return "", ""

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            self.returncode = -15
            return self.returncode

    def popen(*args, **kwargs):
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["start_new_session"] is True
        return TimeoutProcess()

    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(pr_review_catalog.subprocess, "Popen", popen)
    monkeypatch.setattr(
        pr_review_catalog.os,
        "killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(pr_review_catalog.PrReviewCatalogError) as caught:
        pr_review_catalog._run_git(tmp_path, "fetch", timeout=7)

    assert caught.value.code == "git_timeout"
    assert "retry" in caught.value.fix
    assert killed
    assert calls == [
        ("communicate", 7),
        ("wait", 1),
        ("wait", 1),
        ("communicate", 1),
    ]


def test_run_git_timeout_kills_group_when_parent_already_exited(
    monkeypatch, tmp_path: Path
) -> None:
    class LeakedPipeProcess:
        pid = 23456
        returncode = 0
        stdout = None
        stderr = None

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(["git"], timeout)
            raise subprocess.TimeoutExpired(["git"], timeout)

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(
        pr_review_catalog.subprocess,
        "Popen",
        lambda *_args, **_kwargs: LeakedPipeProcess(),
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        pr_review_catalog.os,
        "killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(pr_review_catalog.PrReviewCatalogError) as caught:
        pr_review_catalog._run_git(tmp_path, "fetch", timeout=1)

    assert caught.value.code == "git_timeout"
    assert killed


def test_clear_incomplete_packs_removes_only_temporary_pack_files(
    tmp_path: Path,
) -> None:
    pack_dir = tmp_path / "objects" / "pack"
    pack_dir.mkdir(parents=True)
    temporary = pack_dir / "tmp_pack_123"
    permanent = pack_dir / "pack-abcd.pack"
    temporary.write_bytes(b"partial")
    permanent.write_bytes(b"complete")

    pr_review_catalog._clear_incomplete_packs(tmp_path)

    assert not temporary.exists()
    assert permanent.exists()


def test_validate_configured_checkout_identity_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        pr_review_catalog,
        "_run_git",
        lambda *_args, **_kwargs: "https://github.com/example/other.git",
    )

    with pytest.raises(pr_review_catalog.PrReviewCatalogError) as caught:
        pr_review_catalog._validate_configured_checkout_identity(
            Path("/configured"), "https://github.com/intacct/ia-app.git"
        )

    assert caught.value.code == "source_checkout_identity_mismatch"


def test_cached_manifest_requires_exact_identity_and_target(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("not: a workspace manifest\n", encoding="utf-8")
    source_root = tmp_path / "source.git"

    assert not pr_review_catalog._cached_manifest_matches(
        manifest,
        repo_key="ia-main",
        expected_remote="https://github.com/intacct/ia-app.git",
        source_root=source_root,
        target_sha=TARGET,
    )

    manifest.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "repositories": [
                    {
                        "repo_key": "ia-main",
                        "remote_url": "https://github.com/intacct/ia-app.git",
                        "local_root": str(source_root),
                        "tracked_branch": TARGET,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert pr_review_catalog._cached_manifest_matches(
        manifest,
        repo_key="ia-main",
        expected_remote="https://github.com/intacct/ia-app.git",
        source_root=source_root,
        target_sha=TARGET,
    )


def test_configure_reference_objects_writes_valid_alternate(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source.git"
    (source_root / "objects").mkdir(parents=True)
    reference_objects = tmp_path / "reference-objects"
    reference_objects.mkdir()
    monkeypatch.setattr(
        pr_review_catalog,
        "_run_git",
        lambda *_args, **_kwargs: str(reference_objects),
    )

    pr_review_catalog._configure_reference_objects(source_root, tmp_path / "reference")

    assert (source_root / "objects" / "info" / "alternates").read_text(
        encoding="utf-8"
    ) == f"{reference_objects}\n"


def test_resolve_exact_catalog_uses_verified_cache_hit(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "ia-main" / TARGET / "catalog.db"
    db.parent.mkdir(parents=True)
    _catalog(db)
    monkeypatch.setattr(
        pr_review_catalog,
        "_prepare_source",
        lambda **_: (Path("manifest"), "configured_checkout"),
    )
    monkeypatch.setattr(
        pr_review_catalog,
        "build_ia_main",
        lambda **_: pytest.fail("verified cache must not rebuild"),
    )

    result = pr_review_catalog.resolve_exact_catalog(
        metadata=_metadata(),
        pr_number=48480,
        manifest_path="manifest",
        cache_root=tmp_path,
    )

    assert result.resolution == "cache_hit"
    assert result.target_revision == TARGET
    assert result.active_db == db


def test_resolve_exact_catalog_builds_missing_target(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        pr_review_catalog,
        "_prepare_source",
        lambda **_: (Path("manifest"), "internal_fetch"),
    )

    build_kwargs: dict[str, object] = {}

    def build(**kwargs):
        build_kwargs.update(kwargs)
        active_db = Path(kwargs["active_db"])
        active_db.unlink(missing_ok=True)
        _catalog(active_db, TARGET)
        return SimpleNamespace(target_commit_sha=TARGET)

    monkeypatch.setattr(pr_review_catalog, "build_ia_main", build)

    result = pr_review_catalog.resolve_exact_catalog(
        metadata=_metadata(),
        pr_number=48480,
        manifest_path="manifest",
        cache_root=tmp_path,
        show_progress=True,
    )

    assert result.resolution == "built"
    assert result.source_resolution == "internal_fetch"
    assert result.active_db.is_file()
    assert build_kwargs["show_progress"] is True


def test_resolve_exact_catalog_wraps_promotion_failure(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        pr_review_catalog,
        "_prepare_source",
        lambda **_: (Path("manifest"), "internal_cache"),
    )
    monkeypatch.setattr(
        pr_review_catalog,
        "build_ia_main",
        lambda **_: (_ for _ in ()).throw(
            CatalogPromotionError("active catalog has an incompatible schema")
        ),
    )

    with pytest.raises(pr_review_catalog.PrReviewCatalogError) as caught:
        pr_review_catalog.resolve_exact_catalog(
            metadata=_metadata(),
            pr_number=48480,
            manifest_path="manifest",
            cache_root=tmp_path,
        )

    assert caught.value.code == "catalog_build_failed"
    assert "retry" in caught.value.fix


def test_resolve_exact_catalog_rebuilds_mismatched_cache(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "ia-main" / TARGET / "catalog.db"
    db.parent.mkdir(parents=True)
    _catalog(db, BASE)
    monkeypatch.setattr(
        pr_review_catalog,
        "_prepare_source",
        lambda **_: (Path("manifest"), "configured_checkout"),
    )

    def build(**kwargs):
        active_db = Path(kwargs["active_db"])
        active_db.unlink(missing_ok=True)
        _catalog(active_db, TARGET)
        return SimpleNamespace(target_commit_sha=TARGET)

    monkeypatch.setattr(pr_review_catalog, "build_ia_main", build)

    result = pr_review_catalog.resolve_exact_catalog(
        metadata=_metadata(),
        pr_number=48480,
        manifest_path="manifest",
        cache_root=tmp_path,
    )

    assert result.resolution == "built"
    pr_review_catalog.verify_catalog(
        result.active_db, repo_key="ia-main", target_sha=TARGET
    )


def test_fetch_source_combines_missing_exact_sha_fetches(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source.git"
    source_root.mkdir()
    (source_root / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def run_git(root, *args, **kwargs):
        calls.append(args)
        if args == ("rev-parse", "--is-bare-repository"):
            return "true"
        if args[:2] == ("remote", "get-url"):
            return "https://github.com/intacct/ia-app.git"
        return ""

    monkeypatch.setattr(pr_review_catalog, "_run_git", run_git)
    monkeypatch.setattr(
        pr_review_catalog,
        "resolve_commit_sha",
        lambda root, sha: (_ for _ in ()).throw(
            pr_review_catalog.SourceSnapshotError("missing")
        ),
    )
    monkeypatch.setattr(pr_review_catalog, "_source_has_commits", lambda *args: True)

    pr_review_catalog._fetch_source(
        source_root,
        remote_url="https://github.com/intacct/ia-app.git",
        pr_number=48480,
        base_sha=BASE,
        target_sha=TARGET,
        base_branch="main",
    )

    exact_fetches = [
        call
        for call in calls
        if call[:4] == ("fetch", "--no-tags", "--no-write-fetch-head", "origin")
    ]
    assert exact_fetches[-1] == (
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        "origin",
        f"{BASE}:refs/pr-review/base-sha",
        f"{TARGET}:refs/pr-review/head-sha",
    )


def test_fetch_source_uses_reference_objects_for_missing_target(tmp_path: Path) -> None:
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    reference = tmp_path / "reference.git"
    source = tmp_path / "source.git"
    for path in (work, remote, reference):
        path.mkdir()
    _git(work, "init")
    _git(remote, "init", "--bare")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "tracked.txt")
    _git(work, "commit", "-m", "base")
    base = _git(work, "rev-parse", "HEAD")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "origin", f"{base}:refs/heads/main")
    _git(reference, "init", "--bare")
    _git(work, "push", str(reference), f"{base}:refs/heads/main")

    (work / "tracked.txt").write_text("target\n", encoding="utf-8")
    _git(work, "add", "tracked.txt")
    _git(work, "commit", "-m", "target")
    target = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "origin", f"{target}:refs/pull/48480/head")

    pr_review_catalog._fetch_source(
        source,
        remote_url=str(remote),
        pr_number=48480,
        base_sha=base,
        target_sha=target,
        base_branch="main",
        reference_root=reference,
    )

    assert pr_review_catalog._source_has_commits(source, base, target)
    assert (source / "objects" / "info" / "alternates").is_file()
    assert _git(source, "rev-parse", "refs/pr-review/head") == target
