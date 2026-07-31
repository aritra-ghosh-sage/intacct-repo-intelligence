"""Committed Git change collection for ownership-safe catalog deltas."""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from config import EXCLUDE_DIRS, INCLUDE_EXTENSIONS

DELTA_CONTRACT_VERSION = 2


class ChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass(frozen=True)
class ChangedPath:
    change_type: ChangeType
    old_path: str | None
    new_path: str | None
    old_blob_sha: str | None = None
    new_blob_sha: str | None = None

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True)
class RepositoryChangeSet:
    repo_key: str
    base_commit_sha: str | None
    target_commit_sha: str
    requested_mode: str
    effective_mode: str
    changed_paths: tuple[ChangedPath, ...] = ()
    fallback_reason: str | None = None

    @property
    def is_noop(self) -> bool:
        return self.effective_mode == "noop"


class DeltaUnavailable(RuntimeError):
    """A forced delta cannot prove a safe committed base."""


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise DeltaUnavailable(
            f"git {' '.join(args)} could not run in {root}: {exc}"
        ) from exc
    if check and result.returncode:
        raise DeltaUnavailable(
            result.stderr.strip() or f"git {' '.join(args)} failed in {root}"
        )
    return result


def normalize_repo_path(path: str) -> str:
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DeltaUnavailable(f"repository path is not valid UTF-8: {path!r}") from exc
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized == "." or normalized.startswith(("../", "/")):
        raise DeltaUnavailable(f"repository path escapes checkout: {path!r}")
    return normalized


def path_is_in_scan_scope(path: str) -> bool:
    normalized = normalize_repo_path(path)
    parts = PurePosixPath(normalized).parts[:-1]
    for part in parts:
        if any(fnmatch.fnmatch(part, pattern) for pattern in EXCLUDE_DIRS):
            return False
    return PurePosixPath(normalized).suffix.lower() in INCLUDE_EXTENSIONS


def verify_clean_committed_checkout(root: Path, tracked_branch: str) -> str:
    if _git(root, "status", "--porcelain").stdout.strip():
        raise DeltaUnavailable(f"repository checkout is dirty: {root}")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    current_branch = _git(root, "symbolic-ref", "--short", "HEAD", check=False)
    if current_branch.returncode or current_branch.stdout.strip() != tracked_branch:
        actual = current_branch.stdout.strip() or "detached HEAD"
        raise DeltaUnavailable(
            f"checkout branch {actual!r} does not match configured branch {tracked_branch!r}"
        )
    branch = _git(root, "rev-parse", "--verify", tracked_branch).stdout.strip()
    if head != branch:
        raise DeltaUnavailable(
            f"HEAD {head} does not match configured branch {tracked_branch} ({branch})"
        )
    return head


def _blob_sha(root: Path, revision: str, path: str) -> str:
    result = _git(root, "rev-parse", f"{revision}:{path}", check=False)
    if result.returncode:
        diagnostic = result.stderr.strip() or "git blob lookup failed"
        raise DeltaUnavailable(
            f"blob unavailable for {revision}:{path} in {root}: {diagnostic}"
        )
    blob_sha = result.stdout.strip()
    if not blob_sha:
        raise DeltaUnavailable(
            f"blob unavailable for {revision}:{path} in {root}: empty object id"
        )
    return blob_sha


def _raw_changed_paths(root: Path, base: str, target: str) -> list[tuple[str, ...]]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                f"{base}..{target}",
            ],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise DeltaUnavailable(f"git diff could not run in {root}: {exc}") from exc
    if result.returncode:
        raise DeltaUnavailable(
            result.stderr.decode(errors="replace").strip() or "git diff failed"
        )
    fields = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    records: list[tuple[str, ...]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        kind = status[:1]
        if kind not in {"A", "D", "M", "R", "T"}:
            raise DeltaUnavailable(f"unsupported status {status!r} in git diff output")
        if kind == "R":
            if index + 1 >= len(fields):
                raise DeltaUnavailable("malformed rename record from git diff")
            records.append((status, fields[index], fields[index + 1]))
            index += 2
        else:
            if index >= len(fields):
                raise DeltaUnavailable("malformed path record from git diff")
            records.append((status, fields[index]))
            index += 1
    return records


def collect_changed_paths(
    root: Path, base_commit_sha: str, target_commit_sha: str
) -> tuple[ChangedPath, ...]:
    changes: list[ChangedPath] = []
    for record in _raw_changed_paths(root, base_commit_sha, target_commit_sha):
        status = record[0]
        if status.startswith("R"):
            old_path = normalize_repo_path(record[1])
            new_path = normalize_repo_path(record[2])
            old_in = path_is_in_scan_scope(old_path)
            new_in = path_is_in_scan_scope(new_path)
            if old_in and new_in:
                changes.append(
                    ChangedPath(
                        ChangeType.RENAMED,
                        old_path,
                        new_path,
                        _blob_sha(root, base_commit_sha, old_path),
                        _blob_sha(root, target_commit_sha, new_path),
                    )
                )
            elif old_in:
                changes.append(
                    ChangedPath(
                        ChangeType.DELETED,
                        old_path,
                        None,
                        _blob_sha(root, base_commit_sha, old_path),
                        None,
                    )
                )
            elif new_in:
                changes.append(
                    ChangedPath(
                        ChangeType.ADDED,
                        None,
                        new_path,
                        None,
                        _blob_sha(root, target_commit_sha, new_path),
                    )
                )
            continue
        path = normalize_repo_path(record[1])
        if not path_is_in_scan_scope(path):
            continue
        if status.startswith("A"):
            changes.append(
                ChangedPath(
                    ChangeType.ADDED,
                    None,
                    path,
                    None,
                    _blob_sha(root, target_commit_sha, path),
                )
            )
        elif status.startswith("D"):
            changes.append(
                ChangedPath(
                    ChangeType.DELETED,
                    path,
                    None,
                    _blob_sha(root, base_commit_sha, path),
                    None,
                )
            )
        elif status.startswith(("M", "T")):
            changes.append(
                ChangedPath(
                    ChangeType.MODIFIED,
                    path,
                    path,
                    _blob_sha(root, base_commit_sha, path),
                    _blob_sha(root, target_commit_sha, path),
                )
            )
    return tuple(
        sorted(changes, key=lambda change: (change.path, change.change_type.value))
    )


def collect_repository_change_set(
    *,
    repo_key: str,
    root: Path,
    tracked_branch: str,
    base_commit_sha: str | None,
    requested_mode: str = "auto",
    target_commit_sha: str | None = None,
) -> RepositoryChangeSet:
    if requested_mode not in {"full", "auto", "delta"}:
        raise ValueError(f"unsupported refresh mode: {requested_mode}")
    target = target_commit_sha or verify_clean_committed_checkout(root, tracked_branch)
    if requested_mode == "full":
        return RepositoryChangeSet(repo_key, base_commit_sha, target, "full", "full")

    reason: str | None = None
    if not base_commit_sha:
        reason = "no indexed base SHA"
    else:
        exists = _git(
            root, "cat-file", "-e", f"{base_commit_sha}^{{commit}}", check=False
        )
        if exists.returncode:
            if exists.returncode == 1:
                reason = "base commit unavailable"
            else:
                diagnostic = exists.stderr.strip() or "git cat-file failed"
                raise DeltaUnavailable(f"{repo_key}: {diagnostic}")
        else:
            ancestor = _git(
                root,
                "merge-base",
                "--is-ancestor",
                base_commit_sha,
                target,
                check=False,
            )
            if ancestor.returncode == 1:
                reason = "base is not an ancestor of HEAD"
            elif ancestor.returncode:
                diagnostic = ancestor.stderr.strip() or "git merge-base failed"
                raise DeltaUnavailable(f"{repo_key}: {diagnostic}")

    if reason is not None:
        if requested_mode == "delta":
            raise DeltaUnavailable(f"{repo_key}: {reason}")
        return RepositoryChangeSet(
            repo_key, base_commit_sha, target, requested_mode, "full", (), reason
        )

    if target == base_commit_sha:
        return RepositoryChangeSet(
            repo_key,
            base_commit_sha,
            target,
            requested_mode,
            "noop",
        )

    changes = collect_changed_paths(root, str(base_commit_sha), target)
    return RepositoryChangeSet(
        repo_key,
        base_commit_sha,
        target,
        requested_mode,
        "delta",
        changes,
    )


# Short alias for callers/tests that already establish repository context.
collect_change_set = collect_repository_change_set
