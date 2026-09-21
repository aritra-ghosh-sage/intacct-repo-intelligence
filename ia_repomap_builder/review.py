"""Exact PR acquisition using a caller-owned repository and retained worktrees."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import BuildResult, PrepareRepoMapRequest
from .pr_analysis import (
    PRAnalysisRequestV1,
    load_bedrock_settings,
    run_pr_analysis,
)
from .pr_analysis_skills import (
    RIPWIRE_INITIAL_SKILL_PROFILES,
    RipwireSkillPolicy,
    SkillLoadError,
    load_selected_ripwire_skills,
)
from .readiness import prepare_repomap


class ReviewSetupError(RuntimeError):
    """Raised when a PR cannot be resolved into a safe exact checkout."""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
REQUIRED_RIPWIRE_SKILLS = RIPWIRE_INITIAL_SKILL_PROFILES


@dataclass(frozen=True)
class ReviewRequest:
    """Host-owned input for one self-hosted PR review."""

    pr_url: str
    repo_root: Path
    workspace: Path | None = None
    inspect: bool = False

    def normalised(self) -> ReviewRequest:
        return ReviewRequest(
            pr_url=self.pr_url,
            repo_root=Path(self.repo_root).expanduser().resolve(),
            workspace=(
                Path(self.workspace).expanduser().resolve()
                if self.workspace is not None
                else None
            ),
            inspect=bool(self.inspect),
        )


@dataclass(frozen=True)
class ReviewSetupResult:
    """Host-owned setup outcome and exact checkout identity."""

    status: str
    metadata: PRMetadata | None = None
    checkout: ReviewCheckout | None = None
    artifact_root: Path | None = None
    report_directory: Path | None = None
    readiness: BuildResult | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    remediation: tuple[str, ...] = ()
    skill_policy: RipwireSkillPolicy | None = field(default=None, repr=False, compare=False)

    @property
    def base_sha(self) -> str | None:
        return self.metadata.base_sha if self.metadata else None

    @property
    def head_sha(self) -> str | None:
        return self.metadata.head_sha if self.metadata else None

    def as_dict(self) -> dict[str, Any]:
        readiness = self.readiness.as_dict() if self.readiness is not None else None
        return {
            "status": self.status,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "artifact_root": str(self.artifact_root) if self.artifact_root else None,
            "report_directory": str(self.report_directory) if self.report_directory else None,
            "worktree": str(self.checkout.worktree) if self.checkout else None,
            "identity": dict(self.identity),
            "readiness": readiness,
            "remediation": list(self.remediation),
        }


@dataclass(frozen=True)
class ReviewRunResult:
    """Host-owned public outcome for one review execution."""

    status: str
    assessment: str
    base_sha: str | None = None
    head_sha: str | None = None
    report_directory: Path | None = None
    report_status: str | None = None
    report_assessment: str | None = None
    report_files: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    setup: ReviewSetupResult | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "assessment": self.assessment,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "report_directory": str(self.report_directory) if self.report_directory else None,
            "report": {
                "status": self.report_status,
                "assessment": self.report_assessment,
                "files": list(self.report_files),
            },
            "remediation": list(self.remediation),
        }


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


def resolve_pr(pr_url: str, *, runner: CommandRunner = _default_runner) -> PRMetadata:
    """Resolve immutable PR identity through authenticated ``gh``."""

    url_repository, url_number = _github_pr_url(pr_url)
    completed = _run(
        runner,
        ["gh", "pr", "view", pr_url, "--json", "number,baseRefOid,headRefOid"],
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
    return PRMetadata(
        pr_url,
        number,
        base_sha.lower(),
        head_sha.lower(),
        url_repository,
        f"https://github.com/{url_repository}.git",
    )


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


def _skill_policy(loaded: Any) -> RipwireSkillPolicy:
    """Adapt Slice 2 loader results to the current coordinator policy shape."""

    if isinstance(loaded, RipwireSkillPolicy):
        return loaded
    policy = getattr(loaded, "policy", None)
    if isinstance(policy, RipwireSkillPolicy):
        return policy
    if isinstance(loaded, dict) and isinstance(loaded.get("policy"), RipwireSkillPolicy):
        return loaded["policy"]
    if isinstance(loaded, (tuple, list)):
        try:
            return RipwireSkillPolicy(
                enabled=True,
                requested_profiles=("change_check", "write_tests", "orient"),
                loaded_skills=tuple(loaded),
            )
        except TypeError:
            pass
    # A future compatible policy may be returned as loaded guidance/defaults.
    guidance = getattr(loaded, "guidance", None)
    if isinstance(loaded, dict):
        guidance = loaded.get("guidance", guidance)
    kwargs: dict[str, Any] = {
        "enabled": True,
        "requested_profiles": ("change_check", "write_tests", "orient"),
    }
    if guidance:
        for field_name in ("loaded_guidance", "guidance"):
            try:
                return RipwireSkillPolicy(**kwargs, **{field_name: guidance})
            except TypeError:
                continue
    return RipwireSkillPolicy(**kwargs)


def _safe_repository_name(repository: str) -> str:
    value = "".join(character if character.isalnum() else "-" for character in repository)
    return value.strip("-") or "repository"


def _report_files(report_directory: Path | None) -> tuple[str, ...]:
    if report_directory is None or not report_directory.is_dir():
        return ()
    return tuple(
        sorted(
            str(path.relative_to(report_directory))
            for path in report_directory.rglob("*")
            if path.is_file()
        )
    )


def _is_gh_unavailable(error: ReviewSetupError) -> bool:
    detail = str(error).lower()
    markers = (
        "authentication", "not logged into", "bad credentials", "rate limit",
        "service unavailable", "temporarily unavailable", "connection refused",
        "connection reset", "timed out", "timeout", "http 502", "http 503",
        "http 504", "no such file or directory", "command not found",
    )
    return "gh pr view" in detail and any(marker in detail for marker in markers)


def _setup_remediation(error: Exception, *, unavailable: bool = False) -> tuple[str, ...]:
    if isinstance(error, SkillLoadError):
        return (
            "Ripwire skill prerequisites are unavailable; configure RIPWIRE_SKILLS_DIR "
            "with the three approved skills, then rerun.",
        )
    if unavailable:
        return (
            "GitHub CLI or its service is unavailable; check `gh auth status` and "
            "network access, then rerun.",
        )
    return (str(error), "Correct the PR URL, origin, checkout, or setup prerequisite, then rerun.")


def _setup_identity(metadata: PRMetadata, checkout: ReviewCheckout) -> dict[str, Any]:
    return {
        "repository": metadata.repository,
        "number": metadata.number,
        "base_sha": metadata.base_sha,
        "head_sha": metadata.head_sha,
        "merge_base": checkout.merge_base,
        "worktree": str(checkout.worktree),
        "reused": checkout.reused,
    }


def setup_review(
    request: ReviewRequest,
    *,
    runner: CommandRunner = _default_runner,
    skill_loader: Callable[[Path, Sequence[str]], Any] | None = None,
) -> ReviewSetupResult:
    """Resolve, acquire, prepare, and return one exact self-hosted review setup."""

    request = request.normalised()
    metadata: PRMetadata | None = None
    checkout: ReviewCheckout | None = None
    artifact_root: Path | None = None
    report_directory: Path | None = None
    try:
        metadata = resolve_pr(request.pr_url, runner=runner)
        checkout = acquire_review_checkout(
            metadata,
            repo=request.repo_root,
            workspace=request.workspace,
            runner=runner,
        )
        artifact_root = checkout.workspace_root / "artifacts"
        report_directory = (
            checkout.workspace_root
            / "reports"
            / _safe_repository_name(metadata.repository)
            / metadata.head_sha
            / metadata.base_sha
            / uuid.uuid4().hex
        )

        skills_root = os.environ.get("RIPWIRE_SKILLS_DIR")
        if not skills_root:
            raise SkillLoadError("RIPWIRE_SKILLS_DIR is not configured")
        loaded = (skill_loader or load_selected_ripwire_skills)(
            Path(skills_root), REQUIRED_RIPWIRE_SKILLS
        )
        policy = _skill_policy(loaded)
        with marker_env():
            readiness = prepare_repomap(
                PrepareRepoMapRequest(checkout.worktree, artifact_root)
            )
        identity = _setup_identity(metadata, checkout)
        if readiness.status != "ok":
            status = readiness.status if readiness.status in {"unavailable", "error"} else "error"
            return ReviewSetupResult(
                status=status,
                metadata=metadata,
                checkout=checkout,
                artifact_root=artifact_root,
                report_directory=report_directory,
                readiness=readiness,
                identity=identity,
                remediation=tuple(readiness.diagnostics) or (
                    "Review preparation did not complete; inspect readiness diagnostics and rerun.",
                ),
                skill_policy=policy,
            )
        return ReviewSetupResult(
            status="ok",
            metadata=metadata,
            checkout=checkout,
            artifact_root=artifact_root,
            report_directory=report_directory,
            readiness=readiness,
            identity=identity,
            skill_policy=policy,
        )
    except SkillLoadError as exc:
        return ReviewSetupResult(
            status="unavailable",
            metadata=metadata,
            checkout=checkout,
            artifact_root=artifact_root,
            report_directory=report_directory,
            identity=_setup_identity(metadata, checkout) if metadata and checkout else {},
            remediation=_setup_remediation(exc),
        )
    except ReviewSetupError as exc:
        unavailable = _is_gh_unavailable(exc)
        return ReviewSetupResult(
            status="unavailable" if unavailable else "error",
            metadata=metadata,
            checkout=checkout,
            artifact_root=artifact_root,
            report_directory=report_directory,
            identity=_setup_identity(metadata, checkout) if metadata and checkout else {},
            remediation=_setup_remediation(exc, unavailable=unavailable),
        )
    except (OSError, TypeError, ValueError) as exc:
        return ReviewSetupResult(
            status="error",
            metadata=metadata,
            checkout=checkout,
            artifact_root=artifact_root,
            report_directory=report_directory,
            identity=_setup_identity(metadata, checkout) if metadata and checkout else {},
            remediation=_setup_remediation(exc),
        )


def run_review(
    request: ReviewRequest,
    *,
    setup: Callable[..., ReviewSetupResult] | None = None,
    settings_loader: Callable[[], Any] | None = None,
    analysis_runner: Callable[..., Any] | None = None,
) -> ReviewRunResult:
    """Run the analysis only after a successful exact self-hosted setup."""

    prepared = (setup or setup_review)(request)
    if prepared.status != "ok" or prepared.metadata is None or prepared.checkout is None:
        return ReviewRunResult(
            status=prepared.status,
            assessment=prepared.status,
            base_sha=prepared.base_sha,
            head_sha=prepared.head_sha,
            report_directory=prepared.report_directory,
            remediation=prepared.remediation,
            setup=prepared,
        )

    try:
        settings = (settings_loader or load_bedrock_settings)()
    except (OSError, TypeError, ValueError) as exc:
        return ReviewRunResult(
            status="unavailable",
            assessment="unavailable",
            base_sha=prepared.base_sha,
            head_sha=prepared.head_sha,
            report_directory=prepared.report_directory,
            remediation=(
                f"Bedrock settings unavailable: {exc}. Set AWS_REGION or "
                "AWS_DEFAULT_REGION and BEDROCK_MODEL_ID, then rerun.",
            ),
            setup=prepared,
        )

    try:
        with marker_env():
            report = (analysis_runner or run_pr_analysis)(
                PRAnalysisRequestV1(
                    schema_="ia-repomap.pr-analysis-request/v1",
                    repo_root=prepared.checkout.worktree,
                    base_ref=prepared.metadata.base_sha,
                    artifact_root=prepared.artifact_root,
                    output_dir=prepared.report_directory,
                ),
                settings=settings,
                allow_source_inspection=request.inspect,
                ripwire_skill_policy=prepared.skill_policy,
            )
        status = report.status if report.status in {"ok", "unavailable", "error"} else "error"
        remediation = tuple(getattr(report, "remediation", ()) or ())
        if not remediation and status != "ok":
            remediation = tuple(getattr(report, "diagnostics", ()) or ())
        return ReviewRunResult(
            status=status,
            assessment=report.assessment,
            base_sha=prepared.base_sha,
            head_sha=prepared.head_sha,
            report_directory=prepared.report_directory,
            report_status=report.status,
            report_assessment=report.assessment,
            report_files=_report_files(prepared.report_directory),
            remediation=remediation,
            setup=prepared,
        )
    except Exception as exc:  # pragma: no cover - public wrapper boundary
        return ReviewRunResult(
            status="error",
            assessment="error",
            base_sha=prepared.base_sha,
            head_sha=prepared.head_sha,
            report_directory=prepared.report_directory,
            remediation=(f"{type(exc).__name__}: {exc}", "Correct the setup or execution prerequisite, then rerun."),
            setup=prepared,
        )


def prepare_repomap_for_cli(repo_root: Path, artifact_root: Path) -> BuildResult:
    """Keep the legacy prepare command's request construction out of the CLI."""

    return prepare_repomap(PrepareRepoMapRequest(repo_root, artifact_root))


__all__ = [
    "PRMetadata",
    "REQUIRED_RIPWIRE_SKILLS",
    "ReviewCheckout",
    "ReviewRequest",
    "ReviewRunResult",
    "ReviewSetupError",
    "ReviewSetupResult",
    "SkillLoadError",
    "acquire_review_checkout",
    "load_selected_ripwire_skills",
    "marker_env",
    "prepare_repomap_for_cli",
    "repomap_config_context",
    "resolve_pr",
    "resolve_pr_metadata",
    "run_review",
    "setup_review",
    "workspace_root",
]
