"""Materialize an exact, filter-independent snapshot of a committed Git tree."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class SourceSnapshotError(RuntimeError):
    """A committed source tree cannot be materialized safely."""


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    mode: int
    object_id: str
    size: int


@dataclass(frozen=True)
class SourceSnapshot:
    repo_key: str
    git_root: Path
    target_sha: str
    snapshot_root: Path
    tracked_blob_bytes: int
    tracked_file_count: int
    entries: tuple[GitTreeEntry, ...] = ()


def _git_bytes(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise SourceSnapshotError(
            f"git {' '.join(args)} could not run in {root}: {exc}"
        ) from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise SourceSnapshotError(message or f"git {' '.join(args)} failed in {root}")
    return result.stdout


def _decode_path(raw: bytes) -> str:
    try:
        path = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SourceSnapshotError("Git tree path is not valid UTF-8") from exc
    if not path or "\x00" in path or path.startswith("/"):
        raise SourceSnapshotError(f"unsafe Git tree path: {path!r}")
    parts = PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SourceSnapshotError(f"unsafe Git tree path: {path!r}")
    return path


def _object_id_length(root: Path) -> int:
    value = _git_bytes(root, "rev-parse", "--show-object-format").decode().strip()
    lengths = {"sha1": 40, "sha256": 64}
    try:
        return lengths[value]
    except KeyError as exc:
        raise SourceSnapshotError(f"unsupported Git object format: {value!r}") from exc


def resolve_commit_sha(root: Path, target_sha: str) -> str:
    """Resolve a Git revision to a validated, immutable commit object ID."""

    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        raise SourceSnapshotError(f"Git root does not exist: {resolved_root}")
    return _resolve_commit(
        resolved_root, target_sha, _object_id_length(resolved_root)
    )


def _resolve_commit(root: Path, target_sha: str, object_id_length: int) -> str:
    commit = (
        _git_bytes(root, "rev-parse", "--verify", f"{target_sha}^{{commit}}")
        .decode()
        .strip()
    )
    if len(commit) != object_id_length or any(
        c not in "0123456789abcdef" for c in commit
    ):
        raise SourceSnapshotError(f"invalid resolved commit object id: {commit!r}")
    return commit


def _read_tree(
    root: Path, commit: str, object_id_length: int
) -> tuple[GitTreeEntry, ...]:
    output = _git_bytes(root, "ls-tree", "-r", "-z", "-l", commit)
    records = output.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    entries: list[GitTreeEntry] = []
    seen: set[str] = set()
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
            raw_mode, raw_type, raw_object_id, raw_size = metadata.split()
        except ValueError as exc:
            raise SourceSnapshotError("malformed git ls-tree record") from exc
        path = _decode_path(raw_path)
        if path in seen:
            raise SourceSnapshotError(f"duplicate Git tree path: {path!r}")
        seen.add(path)
        try:
            mode = int(raw_mode, 8)
        except ValueError as exc:
            raise SourceSnapshotError(f"invalid Git tree mode for {path!r}") from exc
        if mode not in {0o100644, 0o100755}:
            raise SourceSnapshotError(
                f"unsupported Git tree mode {raw_mode.decode(errors='replace')} for {path!r}"
            )
        if raw_type != b"blob":
            raise SourceSnapshotError(
                f"unsupported Git tree object type {raw_type.decode(errors='replace')!r} for {path!r}"
            )
        object_id = raw_object_id.decode("ascii", errors="strict")
        if len(object_id) != object_id_length or any(
            c not in "0123456789abcdef" for c in object_id
        ):
            raise SourceSnapshotError(f"invalid Git blob object id for {path!r}")
        try:
            size = int(raw_size)
        except ValueError as exc:
            raise SourceSnapshotError(f"invalid Git blob size for {path!r}") from exc
        if size < 0:
            raise SourceSnapshotError(f"invalid Git blob size for {path!r}")
        entries.append(GitTreeEntry(path, mode, object_id, size))
    return tuple(sorted(entries, key=lambda entry: entry.path))


def _check_free_space(parent: Path, entries: tuple[GitTreeEntry, ...]) -> None:
    stats = os.statvfs(parent)
    block_size = int(stats.f_frsize or stats.f_bsize)
    raw_required = sum(entry.size for entry in entries) + len(entries) * block_size
    required = (raw_required * 5 + 3) // 4
    available = int(stats.f_bavail) * block_size
    if available < required:
        raise SourceSnapshotError(
            f"insufficient free space for source snapshot: required={required} available={available}"
        )


def _git_blob_hash(data: bytes, algorithm: str) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    digest = hashlib.new(algorithm)
    digest.update(header)
    digest.update(data)
    return digest.hexdigest()


def _read_blobs(
    root: Path, entries: tuple[GitTreeEntry, ...]
) -> Iterator[tuple[GitTreeEntry, bytes]]:
    if not entries:
        return
    try:
        process = subprocess.Popen(
            ["git", "-C", str(root), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise SourceSnapshotError(f"git cat-file --batch could not run: {exc}") from exc
    assert process.stdin is not None and process.stdout is not None
    algorithm = "sha1" if len(entries[0].object_id) == 40 else "sha256"
    try:
        for entry in entries:
            try:
                process.stdin.write(entry.object_id.encode("ascii") + b"\n")
                process.stdin.flush()
            except OSError as exc:
                raise SourceSnapshotError(
                    f"git cat-file request failed for {entry.path!r}: {exc}"
                ) from exc
            header = process.stdout.readline()
            fields = header.rstrip(b"\n").split()
            if len(fields) != 3 or fields[1] != b"blob":
                raise SourceSnapshotError(
                    f"missing or wrong-type Git object for {entry.path!r}: {header!r}"
                )
            object_id = fields[0].decode("ascii", errors="replace")
            try:
                size = int(fields[2])
            except ValueError as exc:
                raise SourceSnapshotError(
                    f"invalid cat-file size for {entry.path!r}"
                ) from exc
            data = process.stdout.read(size)
            terminator = process.stdout.read(1)
            if object_id != entry.object_id or size != entry.size or len(data) != size:
                raise SourceSnapshotError(
                    f"Git blob metadata mismatch for {entry.path!r}"
                )
            if terminator != b"\n":
                raise SourceSnapshotError(
                    f"malformed cat-file response for {entry.path!r}"
                )
            if _git_blob_hash(data, algorithm) != entry.object_id:
                raise SourceSnapshotError(f"Git blob hash mismatch for {entry.path!r}")
            yield entry, data
        process.stdin.close()
        return_code = process.wait()
        if return_code:
            assert process.stderr is not None
            message = process.stderr.read().decode("utf-8", errors="replace").strip()
            raise SourceSnapshotError(message or "git cat-file --batch failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _write_entry(snapshot_root: Path, entry: GitTreeEntry, data: bytes) -> None:
    destination = snapshot_root.joinpath(*PurePosixPath(entry.path).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    current = destination.parent
    while current != snapshot_root:
        if current.is_symlink() or not current.is_dir():
            raise SourceSnapshotError(f"unsafe snapshot directory for {entry.path!r}")
        current = current.parent
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
        os.fchmod(descriptor, 0o755 if entry.mode == 0o100755 else 0o644)
    finally:
        os.close(descriptor)
    if destination.stat().st_size != entry.size:
        raise SourceSnapshotError(f"materialized size mismatch for {entry.path!r}")


@contextmanager
def materialize_source_snapshot(
    repo_key: str,
    git_root: Path,
    target_sha: str,
    temp_parent: Path | None = None,
) -> Iterator[SourceSnapshot]:
    """Yield a temporary tree made only from raw target-commit blob bytes."""

    resolved_git_root = git_root.expanduser().resolve()
    if not resolved_git_root.is_dir():
        raise SourceSnapshotError(f"Git root does not exist: {resolved_git_root}")
    object_id_length = _object_id_length(resolved_git_root)
    commit = resolve_commit_sha(resolved_git_root, target_sha)
    entries = _read_tree(resolved_git_root, commit, object_id_length)
    parent = (temp_parent or Path(tempfile.gettempdir())).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    _check_free_space(parent, entries)
    snapshot_root = Path(
        tempfile.mkdtemp(prefix=f"catalog-source-{repo_key}-", dir=parent)
    )
    try:
        for entry, data in _read_blobs(resolved_git_root, entries):
            _write_entry(snapshot_root, entry, data)
        yield SourceSnapshot(
            repo_key=repo_key,
            git_root=resolved_git_root,
            target_sha=commit,
            snapshot_root=snapshot_root,
            tracked_blob_bytes=sum(entry.size for entry in entries),
            tracked_file_count=len(entries),
            entries=entries,
        )
    finally:
        shutil.rmtree(snapshot_root, ignore_errors=True)
