"""Resolve an exact-target repo-v1 catalog for PR review prompting.

The resolver owns only the disposable PR-review source/catalog cache.  It does
not replace or promote the canonical workspace catalog.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from catalog.refresh_transaction import CatalogPromotionError
from catalog.repo_v1 import _REPO_V1_SCHEMA_CONTRACT, RepoV1Error, build_ia_main
from catalog.repo_v1 import REPO_KEY as REPO_V1_KEY
from catalog.repositories import RepositoryError, load_workspace_manifest
from catalog.repository_lifecycle import normalized_github_identity
from catalog.source_snapshot import SourceSnapshotError, resolve_commit_sha

FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parents[1] / ".cache" / "pr-review"
DEFAULT_GIT_TIMEOUT_SECONDS = 120.0


class PrReviewCatalogError(RuntimeError):
    """A required exact-SHA source or catalog prerequisite is unavailable."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        self.code = code
        self.message = message
        self.fix = fix
        super().__init__(message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message} Fix: {self.fix}"


@dataclass(frozen=True)
class CatalogResolution:
    """Internal paths and bounded status for one exact target revision."""

    target_revision: str
    active_db: Path
    manifest: Path
    resolution: str
    source_resolution: str


def _run_git(
    root: Path,
    *args: str,
    timeout: float = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> str:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    command = ["git", "-C", str(root), *args]
    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "env": environment,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if os.name == "posix":
                for signum in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, signum)
                    except ProcessLookupError:
                        break
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        continue
            else:
                process.kill()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            raise PrReviewCatalogError(
                "git_timeout",
                f"Git did not complete within {timeout:g} seconds while preparing the exact PR revision",
                "verify repository access and GitHub authentication, then retry",
            ) from exc
    except OSError as exc:
        raise PrReviewCatalogError(
            "git_unavailable",
            f"Git could not run while preparing the exact PR revision: {exc}",
            "install Git and retry the PR review command",
        ) from exc
    assert process is not None
    if process.returncode:
        detail = stderr.strip() or stdout.strip() or "command failed"
        raise PrReviewCatalogError(
            "git_source_unavailable",
            f"Git could not obtain the required PR revision: {detail}",
            "verify repository access and GitHub authentication, then retry",
        )
    return stdout.strip()


def _configure_reference_objects(source_root: Path, reference_root: Path) -> None:
    """Make an isolated bare cache reuse the configured checkout's Git objects."""

    object_dir = (
        Path(
            _run_git(
                reference_root,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "objects",
            )
        )
        .expanduser()
        .resolve()
    )
    if not object_dir.is_dir():
        raise PrReviewCatalogError(
            "source_object_store_unavailable",
            "The configured checkout Git object store is unavailable",
            "verify the configured ia-main checkout and retry",
        )
    alternates = source_root / "objects" / "info" / "alternates"
    try:
        alternates.parent.mkdir(parents=True, exist_ok=True)
        current = (
            alternates.read_text(encoding="utf-8").splitlines()
            if alternates.is_file()
            else []
        )
        if str(object_dir) not in current:
            alternates.write_text(
                "\n".join([*current, str(object_dir)]) + "\n",
                encoding="utf-8",
            )
    except OSError as exc:
        raise PrReviewCatalogError(
            "source_cache_unavailable",
            "The internal PR source cache cannot configure the reference object store",
            "verify workspace permissions and available disk space, then retry",
        ) from exc


def _clear_incomplete_packs(source_root: Path) -> None:
    """Remove only unindexed temporary packs left by an interrupted fetch."""

    pack_dir = source_root / "objects" / "pack"
    if not pack_dir.is_dir():
        return
    for path in pack_dir.glob("tmp_pack_*"):
        if path.is_file() or path.is_symlink():
            try:
                path.unlink()
            except OSError as exc:
                raise PrReviewCatalogError(
                    "source_cache_unavailable",
                    "The internal PR source cache contains an incomplete pack that cannot be removed",
                    "verify workspace permissions and retry",
                ) from exc


def _validate_configured_checkout_identity(
    configured_root: Path, expected_remote: str
) -> None:
    try:
        actual_remote = _run_git(configured_root, "remote", "get-url", "origin")
    except PrReviewCatalogError as exc:
        raise PrReviewCatalogError(
            "source_checkout_identity_unavailable",
            "The configured checkout origin cannot be verified before reference reuse",
            "verify the ia-main checkout origin and retry",
        ) from exc
    if normalized_github_identity(actual_remote) != normalized_github_identity(
        expected_remote
    ):
        raise PrReviewCatalogError(
            "source_checkout_identity_mismatch",
            "The configured checkout origin does not match the PR repository",
            "correct the ia-main checkout origin or disable reference reuse, then retry",
        )


def _cached_manifest_matches(
    manifest_path: Path,
    *,
    repo_key: str,
    expected_remote: str,
    source_root: Path,
    target_sha: str,
) -> bool:
    try:
        document = load_workspace_manifest(manifest_path)
    except (RepositoryError, OSError):
        return False
    matches = [
        entry for entry in document["repositories"] if entry.get("repo_key") == repo_key
    ]
    if len(matches) != 1:
        return False
    entry = matches[0]
    try:
        local_root = Path(str(entry["local_root"])).expanduser().resolve()
    except (KeyError, OSError, RuntimeError):
        return False
    return (
        normalized_github_identity(entry.get("remote_url"))
        == normalized_github_identity(expected_remote)
        and local_root == source_root.expanduser().resolve()
        and str(entry.get("tracked_branch", "")).lower() == target_sha
    )


def _full_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise PrReviewCatalogError(
            "required_revision_missing",
            f"GitHub did not provide a full {field} commit SHA",
            "retry after GitHub metadata is available; do not substitute a branch name or short SHA",
        )
    return value.lower()


def _metadata_values(
    metadata: Mapping[str, Any], pr_number: int
) -> tuple[str, str, str, str]:
    pr = metadata.get("pull_request")
    if not isinstance(pr, Mapping):
        raise PrReviewCatalogError(
            "pr_metadata_missing",
            "GitHub PR metadata has no pull_request object",
            "verify gh authentication and access to the requested PR, then retry",
        )
    actual_number = pr.get("number")
    if actual_number != pr_number:
        raise PrReviewCatalogError(
            "pr_metadata_mismatch",
            f"GitHub returned PR metadata for {actual_number!r}, not PR {pr_number}",
            "retry with the PR number returned by GitHub",
        )
    repository = metadata.get("repository")
    if not isinstance(repository, str) or "/" not in repository:
        raise PrReviewCatalogError(
            "repository_identity_missing",
            "GitHub PR metadata has no repository identity",
            "verify the workspace manifest remote URL and GitHub access",
        )
    base = _full_sha(pr.get("base_revision"), "base")
    target = _full_sha(pr.get("target_revision"), "head")
    return repository, base, target, str(pr.get("base_branch") or "")


def _manifest_entry(manifest_path: Path, repo_key: str) -> dict[str, Any]:
    try:
        document = load_workspace_manifest(manifest_path)
    except RepositoryError as exc:
        raise PrReviewCatalogError(
            "manifest_invalid",
            f"The workspace manifest is unavailable or invalid: {exc}",
            "repair config/workspace_repos.yaml and retry",
        ) from exc
    matches = [
        entry for entry in document["repositories"] if entry.get("repo_key") == repo_key
    ]
    if len(matches) != 1:
        raise PrReviewCatalogError(
            "repo_not_found",
            f"The workspace manifest must contain exactly one {repo_key!r} repository",
            "add or correct the ia-main entry in config/workspace_repos.yaml",
        )
    return dict(matches[0])


def _validate_manifest_identity(entry: Mapping[str, Any], repository: str) -> None:
    identity = normalized_github_identity(entry.get("remote_url"))
    if identity != tuple(part.lower() for part in repository.split("/", 1)):
        raise PrReviewCatalogError(
            "repository_identity_mismatch",
            "The workspace manifest remote does not match the GitHub PR repository",
            "correct the ia-main remote_url in config/workspace_repos.yaml and retry",
        )


def _source_has_commits(root: Path, base: str, target: str) -> bool:
    if not root.is_dir():
        return False
    try:
        return (
            resolve_commit_sha(root, base) == base
            and resolve_commit_sha(root, target) == target
        )
    except (SourceSnapshotError, OSError):
        return False


def _safe_ref(value: str) -> bool:
    return (
        bool(value)
        and "\n" not in value
        and "\r" not in value
        and not value.startswith("-")
    )


def _fetch_source(
    source_root: Path,
    *,
    remote_url: str,
    pr_number: int,
    base_sha: str,
    target_sha: str,
    base_branch: str,
    reference_root: Path | None = None,
) -> None:
    source_root.parent.mkdir(parents=True, exist_ok=True)
    if not source_root.exists():
        _run_git(source_root.parent, "init", "--bare", str(source_root))
    if not (source_root / "HEAD").exists():
        raise PrReviewCatalogError(
            "source_cache_invalid",
            "The internal PR source cache is not a valid bare Git repository",
            "remove the internal PR-review cache and retry",
        )
    if reference_root is not None:
        _configure_reference_objects(source_root, reference_root)
    try:
        if _run_git(source_root, "rev-parse", "--is-bare-repository") != "true":
            raise PrReviewCatalogError(
                "source_cache_invalid",
                "The internal PR source cache is not a bare Git repository",
                "remove the internal PR-review cache and retry",
            )
    except PrReviewCatalogError as exc:
        if exc.code == "source_cache_invalid":
            raise
        raise PrReviewCatalogError(
            "source_cache_invalid",
            "The internal PR source cache cannot be validated as a bare Git repository",
            "remove the internal PR-review cache and retry",
        ) from exc
    _clear_incomplete_packs(source_root)
    try:
        actual_remote = _run_git(source_root, "remote", "get-url", "origin")
    except PrReviewCatalogError:
        _run_git(source_root, "remote", "add", "origin", remote_url)
    else:
        if normalized_github_identity(actual_remote) != normalized_github_identity(
            remote_url
        ):
            raise PrReviewCatalogError(
                "source_cache_identity_mismatch",
                "The internal source cache points to a different GitHub repository",
                "remove the internal PR-review cache and retry",
            )

    refspecs: list[str] = [
        f"refs/pull/{pr_number}/head:refs/pr-review/head",
    ]
    if _safe_ref(base_branch):
        refspecs.append(f"refs/heads/{base_branch}:refs/pr-review/base-branch")
    try:
        _run_git(
            source_root,
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "origin",
            *refspecs,
        )
    except PrReviewCatalogError as exc:
        # The exact SHA fetch below provides the useful final diagnostic when
        # a provider does not expose the pull-request or base branch ref.
        if exc.code == "git_timeout":
            raise

    missing: list[tuple[str, str]] = []
    for sha, ref in ((base_sha, "base-sha"), (target_sha, "head-sha")):
        try:
            resolved = resolve_commit_sha(source_root, sha)
        except SourceSnapshotError:
            resolved = ""
        if resolved != sha:
            missing.append((sha, ref))
    if missing:
        exact_refspecs = [f"{sha}:refs/pr-review/{ref}" for sha, ref in missing]
        try:
            _run_git(
                source_root,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "origin",
                *exact_refspecs,
            )
        except PrReviewCatalogError as exc:
            if exc.code == "git_timeout":
                raise
            raise PrReviewCatalogError(
                "source_revision_unavailable",
                "One or more exact required commits are not available from the configured GitHub remote",
                "verify GitHub access and that the PR/base commit still exists, then retry",
            ) from exc
    if not _source_has_commits(source_root, base_sha, target_sha):
        raise PrReviewCatalogError(
            "source_revision_mismatch",
            "The fetched Git objects do not resolve to the exact PR base and head SHAs",
            "refresh GitHub access or remove the internal PR-review cache and retry",
        )


def _write_manifest(
    source_manifest: Path, entry: Mapping[str, Any], source_root: Path, target_sha: str
) -> Path:
    target_manifest = source_manifest / "manifest.yaml"
    target_entry = dict(entry)
    target_entry["local_root"] = str(source_root)
    target_entry["tracked_branch"] = target_sha
    try:
        target_manifest.write_text(
            yaml.safe_dump(
                {"version": 1, "repositories": [target_entry]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise PrReviewCatalogError(
            "cache_unavailable",
            "The internal PR-review cache cannot store its target manifest",
            "verify workspace permissions and available disk space, then retry",
        ) from exc
    return target_manifest


def _prepare_source(
    *,
    manifest_path: Path,
    repo_key: str,
    metadata: Mapping[str, Any],
    pr_number: int,
    target_dir: Path,
) -> tuple[Path, str]:
    repository, base_sha, target_sha, base_branch = _metadata_values(
        metadata, pr_number
    )
    entry = _manifest_entry(manifest_path, repo_key)
    _validate_manifest_identity(entry, repository)
    configured_root = Path(str(entry["local_root"])).expanduser().resolve()
    _validate_configured_checkout_identity(configured_root, str(entry["remote_url"]))
    if _source_has_commits(configured_root, base_sha, target_sha):
        return manifest_path, "configured_checkout"
    source_root = target_dir / "source.git"
    cached_manifest = target_dir / "manifest.yaml"
    if _source_has_commits(source_root, base_sha, target_sha):
        try:
            cached_remote = _run_git(source_root, "remote", "get-url", "origin")
        except PrReviewCatalogError:
            cached_remote = ""
        if (
            cached_remote
            and normalized_github_identity(cached_remote)
            == normalized_github_identity(str(entry["remote_url"]))
            and cached_manifest.is_file()
            and _cached_manifest_matches(
                cached_manifest,
                repo_key=repo_key,
                expected_remote=str(entry["remote_url"]),
                source_root=source_root,
                target_sha=target_sha,
            )
        ):
            return cached_manifest, "internal_cache"
    _fetch_source(
        source_root,
        remote_url=str(entry["remote_url"]),
        pr_number=pr_number,
        base_sha=base_sha,
        target_sha=target_sha,
        base_branch=base_branch,
        reference_root=configured_root,
    )
    return _write_manifest(target_dir, entry, source_root, target_sha), "internal_fetch"


def _actual_schema(conn: sqlite3.Connection) -> dict[str, frozenset[str]]:
    return {
        str(table[0]): frozenset(
            str(column[1])
            for column in conn.execute(f'PRAGMA table_info("{table[0]}")')
        )
        for table in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def verify_catalog(path: Path, *, repo_key: str, target_sha: str) -> None:
    """Verify cached catalog identity and immutable source provenance read-only."""

    if not path.is_file() or path.stat().st_size == 0:
        raise PrReviewCatalogError(
            "catalog_unavailable",
            "The exact-target repo-v1 catalog is missing or empty",
            "retry so the isolated catalog can be rebuilt",
        )
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.DatabaseError as exc:
        raise PrReviewCatalogError(
            "catalog_unreadable",
            "The exact-target catalog is not readable SQLite",
            "retry so the isolated catalog can be rebuilt",
        ) from exc
    try:
        if _actual_schema(conn) != _REPO_V1_SCHEMA_CONTRACT:
            raise PrReviewCatalogError(
                "catalog_schema_mismatch",
                "The cached catalog does not match the current repo-v1 schema",
                "remove the internal PR-review cache and retry",
            )
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PrReviewCatalogError(
                "catalog_integrity_failure",
                "SQLite integrity_check failed for the exact-target catalog",
                "remove the internal PR-review cache and retry",
            )
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PrReviewCatalogError(
                "catalog_foreign_key_failure",
                "SQLite foreign_key_check found invalid catalog relationships",
                "remove the internal PR-review cache and retry",
            )
        builds = conn.execute(
            "SELECT id,source_revisions_json FROM catalog_builds WHERE status='active'"
        ).fetchall()
        repos = conn.execute(
            "SELECT id,repo_key,build_id,target_commit_sha FROM repos"
        ).fetchall()
        if len(builds) != 1 or len(repos) != 1:
            raise PrReviewCatalogError(
                "catalog_ownership_invalid",
                "The exact-target catalog does not have one active build and one repository",
                "remove the internal PR-review cache and retry",
            )
        build = builds[0]
        repo = repos[0]
        actual_sha = str(repo["target_commit_sha"]).lower()
        if repo["repo_key"] != repo_key or actual_sha != target_sha:
            raise PrReviewCatalogError(
                "catalog_revision_mismatch",
                f"The catalog revision {actual_sha!r} does not equal PR head {target_sha!r}",
                "retry so an isolated catalog is built at the exact PR head SHA",
            )
        if int(repo["build_id"]) != int(build["id"]):
            raise PrReviewCatalogError(
                "catalog_ownership_invalid",
                "The repository row is not owned by the active catalog build",
                "remove the internal PR-review cache and retry",
            )
        try:
            revisions = json.loads(str(build["source_revisions_json"]))
        except json.JSONDecodeError as exc:
            raise PrReviewCatalogError(
                "catalog_provenance_invalid",
                "The active build source revision record is invalid JSON",
                "remove the internal PR-review cache and retry",
            ) from exc
        if revisions != {repo_key: target_sha}:
            raise PrReviewCatalogError(
                "catalog_provenance_invalid",
                "The active build source revision record does not equal the PR head SHA",
                "remove the internal PR-review cache and retry",
            )
        schema = _actual_schema(conn)
        for table, columns in schema.items():
            for column in ("source_commit_sha", "target_revision"):
                if column in columns:
                    mismatch = conn.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}"<>?',
                        (target_sha,),
                    ).fetchone()[0]
                    if mismatch:
                        raise PrReviewCatalogError(
                            "catalog_provenance_invalid",
                            f"Catalog table {table}.{column} contains facts from another revision",
                            "remove the internal PR-review cache and retry",
                        )
    except sqlite3.DatabaseError as exc:
        raise PrReviewCatalogError(
            "catalog_unreadable",
            "The exact-target catalog could not be validated",
            "remove the internal PR-review cache and retry",
        ) from exc
    finally:
        conn.close()


@contextmanager
def _target_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = path.open("a+")
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        descriptor.close()


def resolve_exact_catalog(
    *,
    metadata: Mapping[str, Any],
    pr_number: int,
    manifest_path: str | Path = "config/workspace_repos.yaml",
    repo_key: str = REPO_V1_KEY,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    show_progress: bool = False,
) -> CatalogResolution:
    """Discover or build an exact-target catalog in the internal cache."""

    if repo_key != REPO_V1_KEY:
        raise PrReviewCatalogError(
            "unsupported_repository",
            "Automatic PR-review catalog resolution currently supports only ia-main",
            "use repo_key ia-main and retry",
        )
    _, _base_sha, target_sha, _ = _metadata_values(metadata, pr_number)
    target_dir = Path(cache_root).expanduser().resolve() / repo_key / target_sha
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PrReviewCatalogError(
            "cache_unavailable",
            "The internal PR-review cache cannot be created",
            "verify workspace permissions and available disk space, then retry",
        ) from exc
    with _target_lock(target_dir / "build.lock"):
        if show_progress:
            print(
                f"[pr-review] resolving exact source for {target_sha}",
                file=sys.stderr,
                flush=True,
            )
        manifest, source_resolution = _prepare_source(
            manifest_path=Path(manifest_path).expanduser().resolve(),
            repo_key=repo_key,
            metadata=metadata,
            pr_number=pr_number,
            target_dir=target_dir,
        )
        active_db = target_dir / "catalog.db"
        try:
            verify_catalog(active_db, repo_key=repo_key, target_sha=target_sha)
            resolution = "cache_hit"
        except PrReviewCatalogError as exc:
            if exc.code not in {
                "catalog_unavailable",
                "catalog_unreadable",
                "catalog_schema_mismatch",
                "catalog_integrity_failure",
                "catalog_foreign_key_failure",
                "catalog_ownership_invalid",
                "catalog_revision_mismatch",
                "catalog_provenance_invalid",
            }:
                raise
            try:
                if show_progress:
                    print(
                        f"[pr-review] building isolated catalog for {target_sha}",
                        file=sys.stderr,
                        flush=True,
                    )
                build = build_ia_main(
                    manifest_path=manifest,
                    active_db=active_db,
                    target_sha=target_sha,
                    promote=True,
                    show_progress=show_progress,
                )
            except (
                CatalogPromotionError,
                RepoV1Error,
                OSError,
                sqlite3.Error,
            ) as build_exc:
                raise PrReviewCatalogError(
                    "catalog_build_failed",
                    f"The exact-target repo-v1 catalog could not be built: {build_exc}",
                    "verify Git objects, disk space, and repository access, then retry",
                ) from build_exc
            if build.target_commit_sha.lower() != target_sha:
                raise PrReviewCatalogError(
                    "catalog_revision_mismatch",
                    "The catalog builder returned a revision different from the PR head SHA",
                    "retry after removing the internal PR-review cache",
                )
            verify_catalog(active_db, repo_key=repo_key, target_sha=target_sha)
            resolution = "built"
        if show_progress:
            print(
                f"[pr-review] exact catalog ready ({resolution})",
                file=sys.stderr,
                flush=True,
            )
    return CatalogResolution(
        target_revision=target_sha,
        active_db=active_db,
        manifest=manifest,
        resolution=resolution,
        source_resolution=source_resolution,
    )
