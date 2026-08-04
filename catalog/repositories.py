"""Repository registry helpers for the shared catalog.

The registry is deliberately small: it owns stable repository identity and
operator-local checkout configuration.  Extractors should receive a resolved
repository record instead of reading ``config.REPO_PATH`` directly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml


class RepositoryError(ValueError):
    """Raised when workspace repository configuration is invalid."""


_MANIFEST_KEYS = frozenset({"version", "repositories"})
_REPOSITORY_KEYS = frozenset(
    {
        "repo_key",
        "name",
        "kind",
        "language",
        "remote_url",
        "local_root",
        "tracked_branch",
        "enabled",
        "profile",
        "builders",
        "depends_on",
        "rest_automation",
        "storage",
    }
)
_REST_AUTOMATION_KEYS = frozenset({"features_root", "object_mapping"})
_OPTIONAL_TEXT_FIELDS = ("name", "kind", "language", "remote_url")


def _reject_unknown_keys(
    mapping: dict[str, Any], allowed: frozenset[str], context: str
) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        noun = "field" if len(unknown) == 1 else "fields"
        raise RepositoryError(
            f"{context} contains unknown {noun}: {', '.join(unknown)}"
        )


def _required_non_empty_string(entry: dict[str, Any], field: str, context: str) -> str:
    if field not in entry:
        raise RepositoryError(f"{context} is missing required field: {field}")
    value = entry[field]
    if not isinstance(value, str) or not value.strip():
        raise RepositoryError(f"{context} field {field} must be a non-empty string")
    normalized = value.strip()
    entry[field] = normalized
    return normalized


def _normalize_optional_string(entry: dict[str, Any], field: str, context: str) -> None:
    if field not in entry or entry[field] is None:
        return
    value = entry[field]
    if not isinstance(value, str) or not value.strip():
        raise RepositoryError(
            f"{context} field {field} must be null or a non-empty string"
        )
    entry[field] = value.strip()


def _normalize_builder_list(repo_key: str, raw_builders: Any) -> list[str]:
    if not isinstance(raw_builders, list):
        raise RepositoryError(
            f"repository {repo_key} builders must be a list of non-empty strings"
        )
    builders: list[str] = []
    seen: set[str] = set()
    for builder in raw_builders:
        if not isinstance(builder, str) or not builder.strip():
            raise RepositoryError(
                f"repository {repo_key} builders must contain non-empty strings"
            )
        builder_name = builder.strip()
        if builder_name in seen:
            raise RepositoryError(
                f"repository {repo_key} builders contains duplicate builder: {builder_name}"
            )
        seen.add(builder_name)
        builders.append(builder_name)
    return builders


def _normalize_profile_and_validate_builders(
    repo_key: str, raw_profile: Any, builders: list[str]
) -> str | None:
    from scripts.builder_registry import BuilderPlanError, build_plan

    if raw_profile is not None and (
        not isinstance(raw_profile, str) or not raw_profile.strip()
    ):
        raise RepositoryError(
            f"repository {repo_key} field profile must be null or a non-empty string"
        )
    profile = raw_profile.strip() if isinstance(raw_profile, str) else None
    try:
        build_plan(profile or "generic", builders)
    except BuilderPlanError as exc:
        raise RepositoryError(f"repository {repo_key} {exc}") from exc
    return profile


def _normalize_rest_automation(
    repo_key: str, profile: str | None, raw_config: Any
) -> dict[str, str] | None:
    if profile != "rest_automation":
        if raw_config is not None:
            raise RepositoryError(
                f"repository {repo_key} rest_automation is only valid for profile rest_automation"
            )
        return None
    if not isinstance(raw_config, dict):
        raise RepositoryError(
            f"repository {repo_key} requires a rest_automation mapping"
        )
    _reject_unknown_keys(
        raw_config,
        _REST_AUTOMATION_KEYS,
        f"repository {repo_key} rest_automation",
    )
    config: dict[str, str] = {}
    for field in ("features_root", "object_mapping"):
        if field not in raw_config:
            raise RepositoryError(
                f"repository {repo_key} requires rest_automation.{field}"
            )
        value = raw_config[field]
        if not isinstance(value, str) or not value.strip():
            raise RepositoryError(
                f"repository {repo_key} rest_automation.{field} must be a non-empty relative path"
            )
        normalized = value.strip()
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise RepositoryError(
                f"repository {repo_key} rest_automation.{field} must be a relative path"
            )
        if ".." in candidate.parts:
            raise RepositoryError(
                f"repository {repo_key} rest_automation.{field} must stay inside local_root"
            )
        config[field] = normalized
    return config


def rest_automation_paths(entry: dict[str, Any], root: Path) -> tuple[Path, Path]:
    """Resolve and validate manifest-owned Gherkin evidence paths."""
    config = entry.get("rest_automation")
    if not isinstance(config, dict):
        raise RepositoryError(
            f"repository {entry.get('repo_key')} requires a rest_automation mapping"
        )
    values: list[Path] = []
    for key, expected_kind in (
        ("features_root", "directory"),
        ("object_mapping", "file"),
    ):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RepositoryError(
                f"repository {entry.get('repo_key')} rest_automation.{key} must be a non-empty relative path"
            )
        candidate = Path(value)
        if candidate.is_absolute():
            raise RepositoryError(
                f"repository {entry.get('repo_key')} rest_automation.{key} must be relative to local_root"
            )
        resolved = (root / candidate).resolve()
        if not resolved.is_relative_to(root):
            raise RepositoryError(
                f"repository {entry.get('repo_key')} rest_automation.{key} must stay inside local_root"
            )
        if (expected_kind == "directory" and not resolved.is_dir()) or (
            expected_kind == "file" and not resolved.is_file()
        ):
            raise RepositoryError(
                f"repository {entry.get('repo_key')} rest_automation.{key} {expected_kind} does not exist: {resolved}"
            )
        values.append(resolved)
    return values[0], values[1]


def _normalize_dependency_list(
    repo_key: str, raw_dependencies: Any
) -> list[str] | None:
    if raw_dependencies is None:
        return None
    if not isinstance(raw_dependencies, list):
        raise RepositoryError(
            f"repository {repo_key} depends_on must be null or a list of repository keys"
        )
    dependencies: list[str] = []
    seen: set[str] = set()
    for dependency in raw_dependencies:
        if not isinstance(dependency, str) or not dependency.strip():
            raise RepositoryError(
                f"repository {repo_key} depends_on must contain non-empty repository keys"
            )
        dependency_key = dependency.strip()
        if dependency_key == repo_key:
            raise RepositoryError(f"repository {repo_key} cannot depend on itself")
        if dependency_key in seen:
            raise RepositoryError(
                f"repository {repo_key} depends_on contains duplicate repository key: {dependency_key}"
            )
        seen.add(dependency_key)
        dependencies.append(dependency_key)
    return dependencies


def _validate_dependency_cycles(dependencies: dict[str, list[str] | None]) -> None:
    permanent: set[str] = set()
    visiting: list[str] = []
    visiting_set: set[str] = set()

    def visit(repo_key: str) -> None:
        if repo_key in permanent:
            return
        if repo_key in visiting_set:
            cycle_start = visiting.index(repo_key)
            cycle = visiting[cycle_start:] + [repo_key]
            raise RepositoryError(
                "workspace manifest contains a cyclic dependency chain: "
                + " -> ".join(cycle)
            )
        visiting.append(repo_key)
        visiting_set.add(repo_key)
        for dependency in dependencies.get(repo_key) or ():
            visit(dependency)
        visiting.pop()
        visiting_set.remove(repo_key)
        permanent.add(repo_key)

    for repo_key in dependencies:
        visit(repo_key)


def load_workspace_manifest(path: str | Path) -> dict[str, Any]:
    """Load and validate a version 1 workspace repository manifest.

    The returned mapping is suitable for registration.  It intentionally does
    not resolve or inspect checkout paths; that is an indexing-time concern.
    """

    manifest_path = Path(path)
    try:
        document = yaml.safe_load(manifest_path.read_text())
    except OSError as exc:
        raise RepositoryError(
            f"cannot read workspace manifest {manifest_path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise RepositoryError(
            f"invalid YAML in workspace manifest {manifest_path}: {exc}"
        ) from exc

    if not isinstance(document, dict):
        raise RepositoryError("workspace manifest must be a mapping")
    _reject_unknown_keys(document, _MANIFEST_KEYS, "workspace manifest")
    if type(document.get("version")) is not int or document["version"] != 1:
        raise RepositoryError("workspace manifest version must be the integer 1")
    repositories = document.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise RepositoryError(
            "workspace manifest must contain a non-empty repositories list"
        )

    seen: set[str] = set()
    dependencies: dict[str, list[str] | None] = {}
    for entry in repositories:
        if not isinstance(entry, dict):
            raise RepositoryError("each repository entry must be a mapping")
        raw_repo_key = entry.get("repo_key")
        entry_context = (
            f"repository {raw_repo_key.strip()}"
            if isinstance(raw_repo_key, str) and raw_repo_key.strip()
            else "repository entry"
        )
        _reject_unknown_keys(entry, _REPOSITORY_KEYS, entry_context)
        repo_key = _required_non_empty_string(entry, "repo_key", "repository entry")
        if repo_key in seen:
            raise RepositoryError(
                f"duplicate repo_key in workspace manifest: {repo_key}"
            )
        seen.add(repo_key)
        context = f"repository {repo_key}"
        _required_non_empty_string(entry, "local_root", context)
        _required_non_empty_string(entry, "tracked_branch", context)
        for field in _OPTIONAL_TEXT_FIELDS:
            _normalize_optional_string(entry, field, context)

        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise RepositoryError(
                f"repository {repo_key} field enabled must be a boolean"
            )
        entry["enabled"] = enabled

        storage = entry.get("storage", "central")
        if storage not in {"central", "sidecar"}:
            raise RepositoryError(
                f"repository {repo_key} storage must be 'central' or 'sidecar'"
            )
        entry["storage"] = storage

        builders = _normalize_builder_list(repo_key, entry.get("builders", []))
        entry["builders"] = builders
        profile = _normalize_profile_and_validate_builders(
            repo_key, entry.get("profile"), builders
        )
        if storage == "sidecar" and profile != "xml_gateway_automation":
            raise RepositoryError(
                f"repository {repo_key} sidecar storage requires profile xml_gateway_automation"
            )
        if "profile" in entry:
            entry["profile"] = profile

        raw_dependencies = entry.get("depends_on")
        dependencies[repo_key] = _normalize_dependency_list(repo_key, raw_dependencies)
        entry["depends_on"] = dependencies[repo_key]

        rest_config = _normalize_rest_automation(
            repo_key, profile, entry.get("rest_automation")
        )
        if rest_config is not None:
            entry["rest_automation"] = rest_config
    for repo_key, repo_dependencies in dependencies.items():
        for dependency in repo_dependencies or ():
            if dependency not in dependencies:
                raise RepositoryError(
                    f"repository {repo_key} depends on unknown repository: {dependency}"
                )
    _validate_dependency_cycles(dependencies)
    return document


def get_repository(conn: sqlite3.Connection, repo_key: str) -> sqlite3.Row:
    """Return one repository by stable key or raise ``RepositoryError``."""

    row = conn.execute("SELECT * FROM repos WHERE repo_key = ?", (repo_key,)).fetchone()
    if row is None:
        raise RepositoryError(f"unknown repository: {repo_key}")
    return row


def resolve_repository_root(conn: sqlite3.Connection, repo_key: str) -> Path:
    """Return an existing checkout root registered for ``repo_key``.

    This checks only the configured filesystem location.  Git branch/SHA and
    cleanliness checks belong to the refresh coordinator.
    """

    root = Path(get_repository(conn, repo_key)["local_root"]).expanduser()
    if not root.is_dir():
        raise RepositoryError(
            f"repository {repo_key} checkout root does not exist: {root}"
        )
    return root.resolve()


def register_manifest(
    conn: sqlite3.Connection, manifest: dict[str, Any]
) -> list[sqlite3.Row]:
    """Upsert manifest repositories and return their catalog rows.

    The caller owns the transaction so registration can be coordinated with
    migrations or candidate catalog creation.
    """

    rows: list[sqlite3.Row] = []
    for entry in manifest["repositories"]:
        if entry.get("storage", "central") == "sidecar":
            raise RepositoryError(
                f"repository {entry['repo_key']} is sidecar storage and cannot be registered in central SQLite"
            )
        builders = entry.get("builders", [])
        conn.execute(
            """
            INSERT INTO repos (
                repo_key, name, kind, language, remote_url, local_root,
                tracked_branch, enabled, profile, effective_builders_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo_key) DO UPDATE SET
                name=excluded.name, kind=excluded.kind, language=excluded.language,
                remote_url=excluded.remote_url, local_root=excluded.local_root,
                tracked_branch=excluded.tracked_branch, enabled=excluded.enabled,
                profile=excluded.profile, effective_builders_json=excluded.effective_builders_json
            """,
            (
                entry["repo_key"],
                entry.get("name"),
                entry.get("kind"),
                entry.get("language"),
                entry.get("remote_url"),
                entry["local_root"],
                entry["tracked_branch"],
                int(entry.get("enabled", True)),
                entry.get("profile"),
                json.dumps(builders, separators=(",", ":")),
            ),
        )
        rows.append(get_repository(conn, entry["repo_key"]))
    return rows
