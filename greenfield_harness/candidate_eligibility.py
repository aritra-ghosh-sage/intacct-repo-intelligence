"""Read-only, revision-bound candidate eligibility preflight for PR impact."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_PATH = re.compile(r"^[^/][^\\]*$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GATEWAY_REPOSITORIES = {"intacct/ia-gwdata-gl", "intacct/ia-gwdata-sanity"}


class EligibilityError(ValueError):
    """Raised when a candidate seed cannot be interpreted safely."""


def default_seeds() -> list[dict[str, Any]]:
    """Return the fixed, deliberately small eligibility boundary."""

    projects = Path("/Users/aritra.ghosh/projects")
    return [
        {"repository": "intacct/ia-restapi-automation-tests", "repo_key": "ia-restapi-automation-tests", "local_root": str(projects / "ia-restapi-automation-tests"), "revision": "ecd3b7120e516ea04bc9070d38594c2fe39a6495", "test_roots": ["features"]},
        {"repository": "intacct/ia-selenium", "repo_key": "ia-selenium", "local_root": str(projects / "ia-selenium"), "revision": "4ebd9007db47116c405779e06100ba8df7e7bceb", "test_roots": ["src"]},
        {"repository": "intacct/ia-test-automation", "repo_key": "ia-test-automation", "local_root": str(projects / "ia-test-automation"), "revision": "b99fb2b834a363ac9b5028bbb97a5905c8f79b80", "test_roots": ["tests"]},
        {"repository": "intacct/ia-gwdata-gl", "repo_key": "ia-gwdata-gl", "local_root": str(projects / "ia-gwdata-gl"), "revision": "b159255de66a41e368fd67263f72cbc46761a537"},
        {"repository": "intacct/ia-gwdata-sanity", "repo_key": "ia-gwdata-sanity", "local_root": str(projects / "ia-gwdata-sanity"), "revision": "e80abf2bbd9cf0082975ea18485c232caca1959b"},
    ]


def validate_fixed_seeds(seeds: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Allow pin/root overrides, but never expand or shrink the fixed seed boundary."""

    expected = {str(seed["repository"]).lower() for seed in default_seeds()}
    repositories: list[str] = []
    for seed in seeds:
        if not isinstance(seed, Mapping):
            raise EligibilityError("seed must be an object")
        repository = str(seed.get("repository") or "").lower()
        if not _REPOSITORY.fullmatch(repository):
            raise EligibilityError("seed.repository must be an owner/name identity")
        repositories.append(repository)
    actual = set(repositories)
    if len(repositories) != len(actual):
        raise EligibilityError("seed override contains duplicate repositories")
    if actual != expected:
        missing = ", ".join(sorted(expected - actual))
        unexpected = ", ".join(sorted(actual - expected))
        details = [value for value in (f"missing: {missing}" if missing else "", f"unexpected: {unexpected}" if unexpected else "") if value]
        raise EligibilityError("seed override must contain exactly the fixed repositories (" + "; ".join(details) + ")")
    return list(seeds)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EligibilityError(f"local_git_unavailable:{exc}") from exc
    if result.returncode:
        raise EligibilityError(result.stderr.strip() or "local_git_failed")
    return result.stdout.strip()


def _remote_repository(remote: str) -> str | None:
    value = remote.strip().removesuffix(".git")
    for marker in ("github.com:", "github.com/"):
        if marker in value:
            candidate = value.split(marker, 1)[1]
            return candidate.lower() if _REPOSITORY.fullmatch(candidate) else None
    return None


def _safe_root(value: Any) -> str:
    path = str(value or "")
    parsed = Path(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or not _SAFE_PATH.fullmatch(path):
        raise EligibilityError("unsafe_test_root")
    return path


def _roots_at(root: Path, revision: str, roots: Sequence[Any]) -> list[str]:
    validated = sorted({_safe_root(item) for item in roots})
    if not validated:
        raise EligibilityError("missing_test_roots")
    for test_root in validated:
        try:
            mode = _git(root, "cat-file", "-t", f"{revision}:{test_root}")
        except EligibilityError as exc:
            raise EligibilityError(f"missing_test_root:{test_root}") from exc
        if mode != "tree":
            raise EligibilityError(f"test_root_not_directory:{test_root}")
    return validated


def _gateway_roots(root: Path, revision: str) -> list[str]:
    """Derive gateway roots from the pinned checkout, never from a catalog guess."""

    present = set(_git(root, "ls-tree", "-d", "--name-only", revision).splitlines())
    roots = [name for name in ("testdefinitions", "testscripts") if name in present]
    return _roots_at(root, revision, roots)


def _metadata_row(
    seed: Mapping[str, Any], metadata_getter: Callable[[str], Mapping[str, Any]], retrieved_at: str
) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    repository = str(seed.get("repository") or "").lower()
    if not _REPOSITORY.fullmatch(repository):
        raise EligibilityError("seed.repository must be an owner/name identity")
    try:
        metadata = metadata_getter(repository)
        if not isinstance(metadata, Mapping):
            raise EligibilityError("github_metadata_invalid")
    except Exception as exc:  # noqa: BLE001 - inaccessible metadata is retained evidence
        error = {"repository": repository, "error": str(exc)[:500]}
        return ({"repository": repository, "repo_key": str(seed.get("repo_key") or repository.rsplit("/", 1)[1]), "local_root": str(seed.get("local_root") or ""), "revision": str(seed.get("revision") or ""), "test_roots": [], "eligibility_status": "unavailable", "reason": "github_metadata_unavailable", "github_metadata_retrieved_at": retrieved_at, "github_metadata_sha256": _digest(error)}, None)
    return ({"repository": repository, "repo_key": str(seed.get("repo_key") or repository.rsplit("/", 1)[1]), "local_root": str(seed.get("local_root") or ""), "revision": str(seed.get("revision") or ""), "test_roots": [], "eligibility_status": "invalid", "reason": "pending_local_validation", "github_metadata_retrieved_at": retrieved_at, "github_metadata_sha256": _digest(dict(metadata))}, metadata)


def build_eligible_candidates(
    seeds: Sequence[Mapping[str, Any]],
    metadata_getter: Callable[[str], Mapping[str, Any]],
    *,
    retrieved_at: str | None = None,
) -> list[dict[str, Any]]:
    """Validate fixed seeds and return the backwards-compatible candidate list."""

    timestamp = retrieved_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        if not isinstance(seed, Mapping):
            raise EligibilityError("seed must be an object")
        row, metadata = _metadata_row(seed, metadata_getter, timestamp)
        if metadata is None:
            rows.append(row)
            continue
        canonical = str(metadata.get("full_name") or "").lower()
        if canonical != row["repository"]:
            row.update(eligibility_status="invalid", reason="github_canonical_identity_mismatch")
            rows.append(row)
            continue
        if metadata.get("archived") is True:
            if row["repository"] in _GATEWAY_REPOSITORIES:
                root = Path(row["local_root"]).expanduser()
                revision = row["revision"].lower()
                try:
                    if root.is_dir() and _SHA.fullmatch(revision):
                        _git(root, "cat-file", "-e", f"{revision}^{{commit}}")
                        row["test_roots"] = _gateway_roots(root, revision)
                except EligibilityError:
                    # Archival exclusion is authoritative; missing local evidence is retained by
                    # the empty root list and never makes this row eligible.
                    pass
            row.update(eligibility_status="excluded_archived", reason="github_repository_archived")
            rows.append(row)
            continue
        root = Path(row["local_root"]).expanduser()
        revision = row["revision"].lower()
        try:
            if not root.is_dir():
                raise EligibilityError("local_checkout_missing")
            remote = _remote_repository(_git(root, "config", "--get", "remote.origin.url"))
            if remote != row["repository"]:
                raise EligibilityError("local_remote_identity_mismatch")
            if not _SHA.fullmatch(revision):
                raise EligibilityError("invalid_pinned_revision")
            try:
                _git(root, "cat-file", "-e", f"{revision}^{{commit}}")
            except EligibilityError as exc:
                raise EligibilityError("missing_pinned_revision") from exc
            roots = _gateway_roots(root, revision) if row["repository"] in _GATEWAY_REPOSITORIES else _roots_at(root, revision, seed.get("test_roots", []))
        except EligibilityError as exc:
            row.update(eligibility_status="invalid", reason=str(exc))
        else:
            row.update(revision=revision, test_roots=roots, eligibility_status="eligible", reason="github_active_and_local_revision_validated")
        rows.append(row)
    return sorted(rows, key=lambda item: item["repository"])


__all__ = ["EligibilityError", "build_eligible_candidates", "default_seeds", "validate_fixed_seeds"]
