from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from catalog import pr_review_catalog
from catalog.refresh_transaction import CatalogPromotionError

BASE = "a" * 40
TARGET = "b" * 40


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
    def run(*args, **kwargs):
        assert kwargs["timeout"] == 7
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(pr_review_catalog.subprocess, "run", run)

    with pytest.raises(pr_review_catalog.PrReviewCatalogError) as caught:
        pr_review_catalog._run_git(tmp_path, "fetch", timeout=7)

    assert caught.value.code == "git_timeout"
    assert "retry" in caught.value.fix


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
    assert result.source_resolution == "internal_fetch"
    assert result.active_db.is_file()


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
