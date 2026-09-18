"""Exact PR acquisition using a caller-owned repository and retained worktrees."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ReviewSetupError(RuntimeError):
    """Raised when a PR cannot be resolved into a safe exact checkout."""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class PRMetadata:
    """Immutable GitHub PR identity resolved by ``gh``."""

    url: str
    number: int
    base_sha: str
    head_sha: str
    repository: str
    clone_url: str | None = None

    @property
    def head_ref(self) -> str:
        return f"refs/ia-repomap/pr/{self.number}/head"


@dataclass(frozen=True)
class ReviewCheckout:
    """A retained, detached PR worktree derived from a caller-owned checkout."""

    metadata: PRMetadata
    workspace_root: Path
    repository_root: Path
    worktree: Path
    merge_base: str
    reused: bool


def _default_runner(
    arguments: Sequence[str], *, cwd: Path | None = None, check: bool = True,
    capture_output: bool = True, text: bool = True, timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments), cwd=cwd, check=check, capture_output=capture_output,
        text=text, timeout=timeout,
    )


def _run(
    runner: CommandRunner, arguments: Sequence[str], *, cwd: Path | None = None,
    check: bool = True, timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            list(arguments), cwd=cwd, check=check, capture_output=True,
            text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewSetupError(f"command failed to start: {' '.join(arguments)}: {exc}") from exc
    if check and completed.returncode != 0:
        raise ReviewSetupError(f"command failed: {' '.join(arguments)}: {_command_detail(completed)}")
    return completed


def _command_detail(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    return stderr[:500] or stdout[:500] or f"exit {completed.returncode}"


def _stdout(runner: CommandRunner, arguments: Sequence[str], *, cwd: Path | None = None) -> str:
    value = (_run(runner, arguments, cwd=cwd).stdout or "").strip()
    if not value:
        raise ReviewSetupError(f"command returned no output: {' '.join(arguments)}")
    return value


def _github_pr_url(pr_url: str) -> tuple[str, int]:
    parsed = urlparse(pr_url)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
        or len(parts) != 4 or parts[2].lower() != "pull" or not parts[3].isdigit()
        or parsed.query or parsed.fragment
    ):
        raise ReviewSetupError(
            "PR URL must be a canonical GitHub URL such as "
            "https://github.com/owner/repository/pull/123"
        )
    return f"{parts[0]}/{parts[1]}", int(parts[3])


def _repository_slug(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("git@github.com:"):
        candidate = candidate.split(":", 1)[1]
    else:
        parsed = urlparse(candidate)
        if parsed.scheme and parsed.netloc:
            if (parsed.hostname or "").lower() != "github.com":
                return None
            candidate = parsed.path.lstrip("/")
    candidate = candidate.rstrip("/")
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    parts = [part for part in candidate.split("/") if part]
    return "/".join(parts).lower() if len(parts) == 2 else None


def _normalised_location(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("file://"):
        candidate = candidate[7:]
    if "://" not in candidate and not candidate.startswith("git@"):
        try:
            return str(Path(candidate).expanduser().resolve())
        except OSError:
            return candidate.rstrip("/")
    return candidate.rstrip("/").removesuffix(".git").lower()


def _repository_matches(actual: str, metadata: PRMetadata) -> bool:
    actual_slug = _repository_slug(actual)
    if actual_slug is not None:
        return actual_slug == metadata.repository.lower()
    return _normalised_location(actual) == _normalised_location(metadata.clone_url or "")


def _metadata_repository(payload: Any) -> tuple[str, str]:
    base_repository = payload.get("baseRepository")
    if not isinstance(base_repository, dict):
        raise ReviewSetupError("gh PR metadata does not contain baseRepository")
    repository = base_repository.get("nameWithOwner")
    clone_url = base_repository.get("cloneUrl") or base_repository.get("sshUrl")
    if not isinstance(repository, str) or not repository.strip():
        raise ReviewSetupError("gh PR metadata is missing baseRepository.nameWithOwner")
    if not isinstance(clone_url, str) or not clone_url.strip():
        clone_url = f"https://github.com/{repository}.git"
    return repository.strip(), clone_url.strip()


def resolve_pr(pr_url: str, *, runner: CommandRunner = _default_runner) -> PRMetadata:
    """Resolve immutable PR identity through authenticated ``gh``."""

    url_repository, url_number = _github_pr_url(pr_url)
    completed = _run(
        runner,
        ["gh", "pr", "view", pr_url, "--json", "number,baseRefOid,headRefOid,baseRepository"],
    )
    try:
        payload = json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise ReviewSetupError(f"gh returned invalid PR metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewSetupError("gh returned a non-object PR metadata payload")
    base_sha, head_sha, number = (
        payload.get("baseRefOid"), payload.get("headRefOid"), payload.get("number")
    )
    if not isinstance(base_sha, str) or not _FULL_SHA.fullmatch(base_sha.strip()):
        raise ReviewSetupError("gh PR metadata is missing baseRefOid")
    if not isinstance(head_sha, str) or not _FULL_SHA.fullmatch(head_sha.strip()):
        raise ReviewSetupError("gh PR metadata is missing headRefOid")
    if not isinstance(number, int) or number != url_number:
        raise ReviewSetupError("gh PR metadata number does not match the PR URL")
    repository, clone_url = _metadata_repository(payload)
    if repository.lower() != url_repository.lower():
        raise ReviewSetupError("gh baseRepository does not match the PR URL repository")
    return PRMetadata(pr_url, number, base_sha.lower(), head_sha.lower(), repository, clone_url)


resolve_pr_metadata = resolve_pr


def workspace_root(explicit: Path | str | None = None) -> Path:
    """Return the external retained-state root using the v1 precedence order."""

    selected = explicit or os.environ.get("IA_REPOMAP_HOME")
    return (Path(selected).expanduser() if selected else Path.home() / ".cache" / "ia-repomap").resolve()


def _validate_source_repository(source: Path, metadata: PRMetadata, runner: CommandRunner) -> Path:
    if not source.is_dir():
        raise ReviewSetupError(f"--repo is not a directory: {source}")
    root = Path(_stdout(runner, ["git", "rev-parse", "--show-toplevel"], cwd=source)).resolve()
    remote = _stdout(runner, ["git", "remote", "get-url", "origin"], cwd=root)
    if not _repository_matches(remote, metadata):
        raise ReviewSetupError(f"--repo origin does not match {metadata.repository}: {remote}")
    return root


def _ensure_external_workspace(
    workspace: Path, source: Path, registered_worktrees: set[Path]
) -> None:
    for worktree in registered_worktrees | {source}:
        try:
            workspace.relative_to(worktree)
        except ValueError:
            continue
        raise ReviewSetupError(
            "managed workspace must be outside the supplied repository and its registered worktrees: "
            f"{worktree}"
        )


def _fetch_and_verify(metadata: PRMetadata, repository: Path, runner: CommandRunner) -> str:
    """Fetch exact PR objects into the caller repository and verify their identities."""

    _run(
        runner,
        [
            "git", "fetch", "--no-tags", "--force", "origin", metadata.base_sha,
            f"refs/pull/{metadata.number}/head:{metadata.head_ref}",
        ],
        cwd=repository,
        timeout=300,
    )
    base = _stdout(runner, ["git", "rev-parse", "--verify", f"{metadata.base_sha}^{{commit}}"], cwd=repository).lower()
    if base != metadata.base_sha:
        raise ReviewSetupError(f"resolved base SHA differs from gh metadata: {base}")
    head = _stdout(runner, ["git", "rev-parse", "--verify", f"{metadata.head_ref}^{{commit}}"], cwd=repository).lower()
    if head != metadata.head_sha:
        raise ReviewSetupError(f"resolved PR head differs from gh metadata: {head}")
    return _stdout(runner, ["git", "merge-base", metadata.base_sha, metadata.head_ref], cwd=repository).lower()


def _worktree_path(workspace: Path, metadata: PRMetadata) -> Path:
    repository_id = metadata.repository.replace("/", "-").replace(".", "-")
    candidate = workspace / "checkouts" / repository_id / metadata.head_sha / metadata.base_sha
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ReviewSetupError(
            f"retained worktree path escapes the managed workspace: {candidate}; "
            "remove the symlink or choose another workspace"
        ) from exc
    current = workspace
    for component in candidate.relative_to(workspace).parts:
        current /= component
        if current.is_symlink():
            raise ReviewSetupError(
                f"retained worktree path contains a symlink: {current}; "
                "remove the symlink or choose another workspace"
            )
    return resolved


def _create_workspace(workspace: Path) -> None:
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReviewSetupError(
            f"could not create managed workspace {workspace}: {exc}; "
            "choose a writable external --workspace path"
        ) from exc


def _common_git_dir(repository: Path, runner: CommandRunner) -> Path:
    raw = _stdout(runner, ["git", "rev-parse", "--git-common-dir"], cwd=repository)
    return (repository / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()


def _registered_worktrees(repository: Path, runner: CommandRunner) -> set[Path]:
    output = _stdout(runner, ["git", "worktree", "list", "--porcelain"], cwd=repository)
    return {
        Path(line.removeprefix("worktree ")).resolve()
        for line in output.splitlines()
        if line.startswith("worktree ")
    }


def _validate_retained_worktree(
    worktree: Path, metadata: PRMetadata, repository: Path, runner: CommandRunner
) -> None:
    if worktree not in _registered_worktrees(repository, runner):
        raise ReviewSetupError(
            f"retained worktree is not registered: {worktree}; remove or repair this bounded workspace path"
        )
    if _common_git_dir(worktree, runner) != _common_git_dir(repository, runner):
        raise ReviewSetupError(f"retained worktree belongs to a different repository: {worktree}")
    symbolic_head = _run(runner, ["git", "symbolic-ref", "-q", "HEAD"], cwd=worktree, check=False)
    if symbolic_head.returncode == 0:
        raise ReviewSetupError(f"retained worktree is not detached: {worktree}")
    if symbolic_head.returncode != 1:
        raise ReviewSetupError(f"could not verify retained worktree detachment: {_command_detail(symbolic_head)}")
    head = _stdout(runner, ["git", "rev-parse", "HEAD"], cwd=worktree).lower()
    if head != metadata.head_sha:
        raise ReviewSetupError(f"retained worktree HEAD differs from gh metadata: {head}")
    status = _run(runner, ["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree)
    if (status.stdout or "").strip():
        raise ReviewSetupError(f"retained PR worktree is not clean: {worktree}")


def acquire_review_checkout(
    metadata: PRMetadata, *, repo: Path | str, workspace: Path | str | None = None,
    runner: CommandRunner = _default_runner,
) -> ReviewCheckout:
    """Fetch PR evidence into ``repo`` and create or reuse its retained exact worktree.

    This never checks out, resets, merges, or changes files in ``repo``. It does
    update its Git object database, ``FETCH_HEAD``, and the private PR ref.
    """

    source = _validate_source_repository(Path(repo).expanduser().resolve(), metadata, runner)
    root = workspace_root(workspace)
    registered = _registered_worktrees(source, runner)
    _ensure_external_workspace(root, source, registered)
    _create_workspace(root)
    merge_base = _fetch_and_verify(metadata, source, runner)
    worktree = _worktree_path(root, metadata)
    registered = _registered_worktrees(source, runner)
    if worktree.exists():
        _validate_retained_worktree(worktree, metadata, source, runner)
        return ReviewCheckout(metadata, root, source, worktree, merge_base, True)
    if worktree in registered:
        raise ReviewSetupError(
            f"retained worktree registration is stale: {worktree}; run git worktree prune in {source} "
            "or restore the bounded workspace path"
        )
    try:
        worktree.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReviewSetupError(
            f"could not create retained worktree parent {worktree.parent}: {exc}; "
            "choose a writable external --workspace path"
        ) from exc
    try:
        _run(
            runner,
            ["git", "worktree", "add", "--detach", str(worktree), metadata.head_sha],
            cwd=source,
            timeout=120,
        )
    except ReviewSetupError as exc:
        raise ReviewSetupError(
            f"retained worktree creation failed at {worktree}; state was not deleted. "
            f"Inspect that bounded path and git worktree list in {source}: {exc}"
        ) from exc
    _validate_retained_worktree(worktree, metadata, source, runner)
    return ReviewCheckout(metadata, root, source, worktree, merge_base, False)


@contextmanager
def marker_env(config_path: Path | str | None = None) -> Iterator[Path]:
    """Temporarily point ``IA_REPOMAP_CONFIG`` at the bundled marker template."""

    marker = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else Path(__file__).resolve().parent / "templates" / ".ia-repomap.toml"
    )
    if not marker.is_file():
        raise ReviewSetupError(f"repository marker template is missing: {marker}")
    previous = os.environ.get("IA_REPOMAP_CONFIG")
    os.environ["IA_REPOMAP_CONFIG"] = str(marker)
    try:
        yield marker
    finally:
        if previous is None:
            os.environ.pop("IA_REPOMAP_CONFIG", None)
        else:
            os.environ["IA_REPOMAP_CONFIG"] = previous


repomap_config_context = marker_env
