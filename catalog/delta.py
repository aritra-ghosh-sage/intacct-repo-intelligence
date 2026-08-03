"""Committed Git change collection for ownership-safe catalog deltas."""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from config import EXCLUDE_DIRS, INCLUDE_EXTENSIONS

DELTA_CONTRACT_VERSION = 4


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
    old_mode: int | None = None
    new_mode: int | None = None
    old_blob_sha: str | None = None
    new_blob_sha: str | None = None
    rename_score: int | None = None

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
    if not path or "\x00" in path or path.startswith("/"):
        raise DeltaUnavailable(f"repository path escapes checkout: {path!r}")
    parts = PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise DeltaUnavailable(f"repository path escapes checkout: {path!r}")
    return path


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


def _object_id_length(root: Path) -> int:
    object_format = _git(root, "rev-parse", "--show-object-format").stdout.strip()
    try:
        return {"sha1": 40, "sha256": 64}[object_format]
    except KeyError as exc:
        raise DeltaUnavailable(
            f"unsupported Git object format: {object_format!r}"
        ) from exc


def _raw_changed_paths(root: Path, base: str, target: str) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "diff",
                "--raw",
                "-z",
                "-M",
                "--no-abbrev",
                base,
                target,
                "--",
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
    return result.stdout


def _decode_raw_path(raw: bytes) -> str:
    try:
        return normalize_repo_path(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise DeltaUnavailable("repository path is not valid UTF-8") from exc


def _parse_mode(raw: bytes) -> int:
    if len(raw) != 6 or any(value not in b"01234567" for value in raw):
        raise DeltaUnavailable(f"invalid Git mode: {raw!r}")
    return int(raw, 8)


def _validate_object_id(
    raw: bytes, *, present: bool, object_id_length: int
) -> str | None:
    try:
        value = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise DeltaUnavailable("Git object id is not ASCII") from exc
    if len(value) != object_id_length or any(
        c not in "0123456789abcdef" for c in value
    ):
        raise DeltaUnavailable(f"invalid Git object id: {value!r}")
    zero = value == "0" * object_id_length
    if present == zero:
        raise DeltaUnavailable(
            "present Git side has a zero object id"
            if present
            else "absent Git side has a nonzero object id"
        )
    return value if present else None


def _verify_blob(root: Path, object_id: str) -> None:
    exists = _git(root, "cat-file", "-e", object_id, check=False)
    if exists.returncode:
        detail = exists.stderr.strip() or "object is missing"
        raise DeltaUnavailable(f"Git object {object_id} is unavailable: {detail}")
    object_type = _git(root, "cat-file", "-t", object_id, check=False)
    if object_type.returncode:
        detail = object_type.stderr.strip() or "object type is unavailable"
        raise DeltaUnavailable(f"Git object {object_id} cannot be typed: {detail}")
    if object_type.stdout.strip() != "blob":
        raise DeltaUnavailable(
            f"Git object {object_id} has unsupported type {object_type.stdout.strip()!r}"
        )


def _parse_raw_diff(root: Path, output: bytes) -> tuple[ChangedPath, ...]:
    object_id_length = _object_id_length(root)
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if any(field == b"" for field in fields):
        raise DeltaUnavailable("malformed empty field in raw Git diff")
    index = 0
    changes: list[ChangedPath] = []
    seen_paths: set[str] = set()
    blobs: set[str] = set()
    while index < len(fields):
        metadata = fields[index]
        index += 1
        if not metadata.startswith(b":"):
            raise DeltaUnavailable(f"malformed raw Git diff metadata: {metadata!r}")
        parts = metadata[1:].split(b" ")
        if len(parts) != 5:
            raise DeltaUnavailable(f"malformed raw Git diff metadata: {metadata!r}")
        raw_old_mode, raw_new_mode, raw_old_object, raw_new_object, raw_status = parts
        old_mode_value = _parse_mode(raw_old_mode)
        new_mode_value = _parse_mode(raw_new_mode)
        old_present = old_mode_value != 0
        new_present = new_mode_value != 0
        for raw_mode, mode, present in (
            (raw_old_mode, old_mode_value, old_present),
            (raw_new_mode, new_mode_value, new_present),
        ):
            if present and mode not in {0o100644, 0o100755}:
                raise DeltaUnavailable(
                    f"unsupported Git tree mode {raw_mode.decode(errors='replace')}"
                )
            if not present and mode != 0:
                raise DeltaUnavailable("absent Git side has a nonzero mode")
        old_object = _validate_object_id(
            raw_old_object, present=old_present, object_id_length=object_id_length
        )
        new_object = _validate_object_id(
            raw_new_object, present=new_present, object_id_length=object_id_length
        )
        try:
            status = raw_status.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise DeltaUnavailable("Git status is not ASCII") from exc
        kind = status[:1]
        score: int | None = None
        if kind == "R":
            raw_score = status[1:]
            if not raw_score.isdigit():
                raise DeltaUnavailable(f"malformed rename status: {status!r}")
            score = int(raw_score)
            if score < 0 or score > 100:
                raise DeltaUnavailable(f"invalid rename score: {status!r}")
        elif status not in {"A", "D", "M"}:
            raise DeltaUnavailable(f"unsupported status {status!r} in raw Git diff")

        expected_sides = {
            "A": (False, True),
            "D": (True, False),
            "M": (True, True),
            "R": (True, True),
        }[kind]
        if (old_present, new_present) != expected_sides:
            raise DeltaUnavailable(
                f"status {status!r} has invalid old/new mode combination"
            )
        path_count = 2 if kind == "R" else 1
        if index + path_count > len(fields):
            raise DeltaUnavailable(f"malformed path record for status {status!r}")
        paths = tuple(
            _decode_raw_path(value) for value in fields[index : index + path_count]
        )
        index += path_count
        if len(set(paths)) != len(paths) and kind == "R":
            raise DeltaUnavailable("rename old and new paths are identical")
        if any(path in seen_paths for path in paths):
            raise DeltaUnavailable(f"duplicate path in raw Git diff: {paths!r}")
        seen_paths.update(paths)
        if old_object is not None:
            blobs.add(old_object)
        if new_object is not None:
            blobs.add(new_object)
        changes.append(
            ChangedPath(
                change_type={
                    "A": ChangeType.ADDED,
                    "D": ChangeType.DELETED,
                    "M": ChangeType.MODIFIED,
                    "R": ChangeType.RENAMED,
                }[kind],
                old_path=None if kind == "A" else paths[0],
                new_path=None if kind == "D" else paths[-1],
                old_mode=old_mode_value if old_present else None,
                new_mode=new_mode_value if new_present else None,
                old_blob_sha=old_object,
                new_blob_sha=new_object,
                rename_score=score,
            )
        )
    for object_id in sorted(blobs):
        _verify_blob(root, object_id)
    return tuple(
        sorted(
            changes,
            key=lambda change: (
                change.old_path or "",
                change.new_path or "",
                change.change_type.value,
            ),
        )
    )


def collect_changed_paths(
    root: Path, base_commit_sha: str, target_commit_sha: str
) -> tuple[ChangedPath, ...]:
    return _parse_raw_diff(
        root, _raw_changed_paths(root, base_commit_sha, target_commit_sha)
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
