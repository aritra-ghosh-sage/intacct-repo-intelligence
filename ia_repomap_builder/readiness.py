"""Repository declaration, external index preparation, and readiness checks."""

from __future__ import annotations

import json
import os
import hashlib
import shutil
import subprocess
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import (
    PHP_FAMILY_EXTENSIONS,
    REPOMAP_CONFIG_FILENAME,
    REPOMAP_SCHEMA_VERSION,
    BuildResult,
    PrepareRepoMapRequest,
    PrContextRequest,
    RepoMapConfig,
)
from .engines import _ripwire_binary
from .files import resolve_scopes
from .identity import (
    configuration_digest,
    file_digest,
    git_merge_base,
    git_ref_revision,
    git_revision,
    is_dirty,
    repository_id,
)

MANIFEST_SCHEMA = "ia-repomap.manifest/v1"
_ALLOWED_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "engine",
        "scope",
        "token_budget",
        "php_family_extensions",
        "map_php_scope",
    }
)


@dataclass(frozen=True)
class ArtifactLocations:
    directory: Path
    manifest: Path
    lean_cache: Path
    rich_cache: Path


@dataclass(frozen=True)
class _CacheValidation:
    status: str
    diagnostic: str | None = None


def load_repomap_config(repo_root: Path) -> RepoMapConfig:
    """Load and strictly validate a repository-local ``.ia-repomap.toml``."""

    root = repo_root.resolve()
    configured_marker = os.environ.get("IA_REPOMAP_CONFIG")
    marker = Path(configured_marker).resolve() if configured_marker else root / REPOMAP_CONFIG_FILENAME
    if not marker.is_file():
        raise FileNotFoundError(f"repository marker is missing: {marker}")
    try:
        raw = tomllib.loads(marker.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid repository marker: {exc}") from exc
    unknown = sorted(set(raw) - _ALLOWED_CONFIG_KEYS)
    if unknown:
        raise ValueError(f"unknown repository marker keys: {', '.join(unknown)}")

    required = _ALLOWED_CONFIG_KEYS
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"repository marker is missing required keys: {', '.join(missing)}")

    schema_version = raw["schema_version"]
    engine = raw["engine"]
    scope = _string_tuple(raw["scope"], "scope")
    token_budget = raw["token_budget"]
    extensions = _string_tuple(raw["php_family_extensions"], "php_family_extensions")
    map_scope = _string_tuple(raw["map_php_scope"], "map_php_scope")

    if type(schema_version) is not int or schema_version != REPOMAP_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {REPOMAP_SCHEMA_VERSION}")
    if engine != "ripwire":
        raise ValueError("engine must be ripwire for the PR-context slice")
    if type(token_budget) is not int or token_budget <= 0:
        raise ValueError("token_budget must be a positive integer")
    if scope != ("app/source",):
        raise ValueError("PR context currently requires scope to be exactly ['app/source']")
    if map_scope != ("app/source",):
        raise ValueError("map_php_scope must be exactly ['app/source']")
    if not extensions:
        raise ValueError("php_family_extensions must not be empty")
    if any(not extension.startswith(".") or extension != extension.lower() for extension in extensions):
        raise ValueError("php_family_extensions must contain lowercase dotted suffixes")
    if len(set(extensions)) != len(extensions):
        raise ValueError("php_family_extensions must not contain duplicates")
    unsupported = sorted(set(extensions) - PHP_FAMILY_EXTENSIONS)
    if unsupported:
        raise ValueError(f"unsupported Intacct PHP-family extensions: {', '.join(unsupported)}")
    if ".map" not in extensions:
        raise ValueError("php_family_extensions must include .map for the Intacct profile")

    resolve_scopes(root, scope)
    return RepoMapConfig(
        schema_version=schema_version,
        engine=engine,
        scope=scope,
        token_budget=token_budget,
        php_family_extensions=extensions,
        map_php_scope=map_scope,
    )


def prepare_repomap(request: PrepareRepoMapRequest) -> BuildResult:
    """Build a validated external Ripwire index for the current clean revision."""

    started = time.perf_counter()
    setup = _prepare_inputs(request.repo_root, request.artifact_root)
    if isinstance(setup, BuildResult):
        return setup
    root, artifact_root, config, head, engine = setup
    locations = artifact_locations(root, artifact_root, config, head, engine)
    identity = _identity(root, config, head, engine, locations)

    if locations.manifest.is_file():
        manifest = _load_manifest(locations.manifest)
        if manifest is None:
            return BuildResult(
                engine="ripwire",
                status="error",
                diagnostics=[f"artifact manifest is malformed: {locations.manifest}"],
                identity=identity,
            )
        if _manifest_matches(manifest, root, config, head, engine, locations):
            return BuildResult(
                engine="ripwire",
                status="ok",
                diagnostics=["matching revision-bound index already exists"],
                metrics={"prepared": False, "elapsed_ms": _elapsed_ms(started)},
                identity=identity,
            )
        return BuildResult(
            engine="ripwire",
            status="error",
            diagnostics=[f"artifact directory exists with a non-matching manifest: {locations.directory}"],
            identity=identity,
        )
    if locations.directory.exists():
        return BuildResult(
            engine="ripwire",
            status="error",
            diagnostics=[f"artifact directory exists without a manifest: {locations.directory}"],
            identity=identity,
        )

    extension_error = _verify_ripwire_extensions(engine["binary"], config.php_family_extensions)
    if extension_error:
        return BuildResult(
            engine="ripwire",
            status="unavailable",
            diagnostics=[extension_error],
            identity=identity,
        )
    feature_error = _verify_ripwire_pr_history(engine["binary"])
    if feature_error:
        return BuildResult("ripwire", "unavailable", diagnostics=[feature_error], identity=identity)

    locations.directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ia-repomap-staging-", dir=locations.directory.parent))
    try:
        staging_base = staging / "index"
        completed = subprocess.run(
            [engine["binary"], str(root / config.scope[0]), f"--index-out={staging_base}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode != 0:
            return BuildResult(
                engine="ripwire",
                status="error",
                diagnostics=[f"Ripwire index build exited {completed.returncode}: {completed.stderr.strip()[:500]}"],
                identity=identity,
            )
        staged_lean = staging / "index.lean.ripwirecache"
        staged_rich = staging / "index.rich.ripwirecache"
        if not staged_lean.is_file() or not staged_rich.is_file():
            return BuildResult(
                engine="ripwire",
                status="error",
                diagnostics=["Ripwire index build did not produce both lean and rich caches"],
                identity=identity,
            )
        cache_check = _validate_ripwire_lean_cache(engine["binary"], root / config.scope[0], staged_lean)
        if cache_check.status != "ok":
            return BuildResult(
                engine="ripwire",
                status=cache_check.status,
                diagnostics=[cache_check.diagnostic or "Ripwire cache validation failed"],
                identity=identity,
            )
        manifest = _manifest(root, config, head, engine)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, locations.directory)
    except (OSError, subprocess.SubprocessError) as exc:
        return BuildResult(
            engine="ripwire",
            status="error",
            diagnostics=[f"Ripwire index preparation failed: {exc}"],
            identity=identity,
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    diagnostics = ["prepared revision-bound external Ripwire index"]
    if cache_check.diagnostic:
        diagnostics.append(cache_check.diagnostic)
    return BuildResult(
        engine="ripwire",
        status="ok",
        diagnostics=diagnostics,
        metrics={"prepared": True, "elapsed_ms": _elapsed_ms(started)},
        identity=identity,
    )


def check_repomap_readiness(request: PrContextRequest) -> BuildResult:
    """Return whether a PR-context request may use an exact prepared index."""

    prepared = check_prepared_repomap_readiness(
        PrepareRepoMapRequest(request.repo_root, request.artifact_root)
    )
    if prepared.status != "ok":
        return prepared
    root = request.repo_root.resolve()
    if not request.base_ref.strip():
        return BuildResult("ripwire", "error", diagnostics=["base_ref must not be empty"])
    base_revision = git_ref_revision(root, request.base_ref)
    merge_base = git_merge_base(root, request.base_ref)
    if base_revision is None or merge_base is None:
        return BuildResult(
            "ripwire",
            "unavailable",
            diagnostics=[f"base_ref cannot be resolved with HEAD: {request.base_ref}"],
        )
    identity = dict(prepared.identity)
    identity.update(
        {"base_ref": request.base_ref, "base_revision": base_revision, "merge_base": merge_base}
    )
    diagnostics = list(prepared.diagnostics)
    return BuildResult("ripwire", "ok", diagnostics=diagnostics, identity=identity)


def check_prepared_repomap_readiness(request: PrepareRepoMapRequest) -> BuildResult:
    """Validate a clean checkout and its exact external index without a base ref."""

    setup = _prepare_inputs(request.repo_root, request.artifact_root)
    if isinstance(setup, BuildResult):
        return setup
    root, artifact_root, config, head, engine = setup
    locations = artifact_locations(root, artifact_root, config, head, engine)
    identity = _identity(root, config, head, engine, locations)
    if not locations.manifest.is_file():
        return BuildResult(
            "ripwire",
            "unavailable",
            diagnostics=[f"prepared index manifest is missing: {locations.manifest}"],
            identity=identity,
        )
    manifest = _load_manifest(locations.manifest)
    if manifest is None:
        return BuildResult(
            "ripwire",
            "error",
            diagnostics=[f"prepared index manifest is malformed: {locations.manifest}"],
            identity=identity,
        )
    if not _manifest_matches(manifest, root, config, head, engine, locations):
        return BuildResult(
            "ripwire",
            "unavailable",
            diagnostics=["prepared index manifest does not match the current repository identity"],
            identity=identity,
        )
    feature_error = _verify_ripwire_pr_history(engine["binary"])
    if feature_error:
        return BuildResult("ripwire", "unavailable", diagnostics=[feature_error], identity=identity)
    cache_check = _validate_ripwire_lean_cache(
        engine["binary"], root / config.scope[0], locations.lean_cache
    )
    if cache_check.status != "ok":
        return BuildResult(
            "ripwire",
            cache_check.status,
            diagnostics=[cache_check.diagnostic or "Ripwire cache validation failed"],
            identity=identity,
        )
    diagnostics = [cache_check.diagnostic] if cache_check.diagnostic else []
    return BuildResult("ripwire", "ok", diagnostics=diagnostics, identity=identity)


def artifact_locations(
    repo_root: Path,
    artifact_root: Path,
    config: RepoMapConfig,
    head: str,
    engine: dict[str, str],
) -> ArtifactLocations:
    directory = (
        artifact_root.resolve()
        / repository_id(repo_root)
        / head
        / configuration_digest(config)
        / engine["id"]
    )
    return ArtifactLocations(
        directory=directory,
        manifest=directory / "manifest.json",
        lean_cache=directory / "index.lean.ripwirecache",
        rich_cache=directory / "index.rich.ripwirecache",
    )


def _prepare_inputs(
    repo_root: Path, artifact_root: Path
) -> tuple[Path, Path, RepoMapConfig, str, dict[str, str]] | BuildResult:
    root = repo_root.resolve()
    if not root.is_dir():
        return BuildResult("ripwire", "error", diagnostics=[f"repository root is not a directory: {root}"])
    try:
        artifact = artifact_root.resolve()
        artifact.relative_to(root)
    except ValueError:
        artifact = artifact_root.resolve()
    else:
        return BuildResult(
            "ripwire",
            "error",
            diagnostics=["artifact_root must remain outside the repository checkout"],
        )
    try:
        config = load_repomap_config(root)
    except FileNotFoundError as exc:
        return BuildResult("ripwire", "unavailable", diagnostics=[str(exc)])
    except ValueError as exc:
        return BuildResult("ripwire", "error", diagnostics=[str(exc)])

    head = git_revision(root)
    dirty = is_dirty(root)
    if head is None or dirty is None:
        return BuildResult("ripwire", "error", diagnostics=["repository is not a readable Git checkout"])
    if dirty:
        return BuildResult("ripwire", "unavailable", diagnostics=["repository checkout must be clean"])
    binary = _ripwire_binary()
    if binary is None:
        return BuildResult(
            "ripwire",
            "unavailable",
            diagnostics=["Ripwire binary unavailable; set RIPWIRE_BIN to a pinned build"],
        )
    try:
        engine = _engine_identity(binary)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return BuildResult("ripwire", "error", diagnostics=[f"Ripwire version check failed: {exc}"])
    return root, artifact, config, head, engine


def _engine_identity(binary: str) -> dict[str, str]:
    completed = subprocess.run(
        [binary, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ValueError(completed.stderr.strip() or f"exit {completed.returncode}")
    patch = Path(__file__).with_name("patches") / "ripwire-v0.4.0-intacct-repomap.patch"
    patch_digest = file_digest(patch)
    binary_digest = file_digest(Path(binary).resolve())
    version = completed.stdout.strip()
    engine_id = hashlib.sha256(
        f"{version}\n{patch_digest}\n{binary_digest}".encode()
    ).hexdigest()
    return {
        "name": "ripwire",
        "binary": binary,
        "version": version,
        "patch": patch.name,
        "patch_sha256": patch_digest,
        "binary_sha256": binary_digest,
        "features": ["pr-history-commits"],
        "id": engine_id,
    }


def _verify_ripwire_extensions(binary: str, extensions: tuple[str, ...]) -> str | None:
    """Prove that this binary routes every declared Intacct extension as PHP."""

    with tempfile.TemporaryDirectory(prefix="ia-repomap-ripwire-probe-") as directory:
        probe_root = Path(directory)
        expected_paths: list[str] = []
        for index, extension in enumerate(extensions):
            filename = f"Alias{index}{extension}"
            expected_paths.append(filename)
            (probe_root / filename).write_text(
                f"<?php class Alias{index} {{ function method{index}() {{}} }}\n",
                encoding="utf-8",
            )
        try:
            completed = subprocess.run(
                [binary, str(probe_root), "--top-k=200", "--legend=compact"],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"Ripwire extension probe failed: {exc}"
    if completed.returncode != 0:
        return f"Ripwire extension probe exited {completed.returncode}: {completed.stderr.strip()[:500]}"
    try:
        root = ET.fromstring(completed.stdout)
    except ET.ParseError as exc:
        return f"Ripwire extension probe emitted invalid XML: {exc}"
    observed = {node.attrib.get("p") for node in root.iter("f")}
    missing = sorted(path for path in expected_paths if path not in observed)
    if missing:
        return f"Ripwire binary does not recognize configured Intacct extensions: {', '.join(missing)}"
    return None


def _verify_ripwire_pr_history(binary: str) -> str | None:
    """Require bounded PR-history support before an index is consumed."""

    try:
        completed = subprocess.run(
            [binary, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Ripwire feature probe failed: {exc}"
    help_text = f"{completed.stdout}\n{completed.stderr}"
    if "--pr-history-commits=" not in help_text:
        detail = completed.stderr.strip()[:500]
        suffix = f": {detail}" if detail else ""
        return f"Ripwire binary does not support --pr-history-commits{suffix}"
    return None


def _validate_ripwire_lean_cache(
    binary: str, scope_root: Path, lean_cache: Path
) -> _CacheValidation:
    """Refuse a cache Ripwire would silently replace with a cold parse."""

    try:
        completed = subprocess.run(
            [binary, str(scope_root), f"--cache={lean_cache}", "--doctor"],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _CacheValidation("error", f"Ripwire cache validation failed: {exc}")
    try:
        root = ET.fromstring(completed.stdout)
    except ET.ParseError as exc:
        return _CacheValidation("error", f"Ripwire cache validation emitted invalid XML: {exc}")
    if root.tag != "doctor":
        return _CacheValidation("error", "Ripwire cache validation did not emit a doctor report")
    cache_row = next((node for node in root.findall("./c") if node.attrib.get("n") == "index-cache"), None)
    if cache_row is None:
        return _CacheValidation("error", "Ripwire cache validation did not report index-cache status")
    lean_status = cache_row.attrib.get("lean")
    source = cache_row.attrib.get("source")
    if source != "cache-flag" or lean_status != "ok":
        return _CacheValidation(
            "unavailable",
            "prepared lean cache cannot be consumed by the current Ripwire binary: "
            f"source={source or 'missing'}, lean={lean_status or 'missing'}",
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip()[:500]
        warning = f"Ripwire doctor exited {completed.returncode}; named lean cache passed validation"
        if detail:
            warning += f": {detail}"
        return _CacheValidation("ok", warning)
    return _CacheValidation("ok")


def _manifest(root: Path, config: RepoMapConfig, head: str, engine: dict[str, str]) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "repository": {"id": repository_id(root), "revision": head, "dirty": False},
        "configuration": {"scope": list(config.scope), "digest": configuration_digest(config)},
        "engine": {
            "name": engine["name"],
            "version": engine["version"],
            "patch": engine["patch"],
            "patch_sha256": engine["patch_sha256"],
            "binary_sha256": engine["binary_sha256"],
            "id": engine["id"],
            "features": list(engine["features"]),
            "extensions": list(config.php_family_extensions),
        },
        "artifacts": {
            "lean_cache": "index.lean.ripwirecache",
            "rich_cache": "index.rich.ripwirecache",
        },
        "created_at": datetime.now(UTC).isoformat(),
    }


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _manifest_matches(
    manifest: dict[str, Any],
    root: Path,
    config: RepoMapConfig,
    head: str,
    engine: dict[str, str],
    locations: ArtifactLocations,
) -> bool:
    return (
        manifest.get("schema") == MANIFEST_SCHEMA
        and manifest.get("repository")
        == {"id": repository_id(root), "revision": head, "dirty": False}
        and manifest.get("configuration")
        == {"scope": list(config.scope), "digest": configuration_digest(config)}
        and manifest.get("engine")
        == {
            "name": engine["name"],
            "version": engine["version"],
            "patch": engine["patch"],
            "patch_sha256": engine["patch_sha256"],
            "binary_sha256": engine["binary_sha256"],
            "id": engine["id"],
            "features": list(engine["features"]),
            "extensions": list(config.php_family_extensions),
        }
        and manifest.get("artifacts")
        == {"lean_cache": "index.lean.ripwirecache", "rich_cache": "index.rich.ripwirecache"}
        and locations.lean_cache.is_file()
        and locations.rich_cache.is_file()
    )


def _identity(
    root: Path,
    config: RepoMapConfig,
    head: str,
    engine: dict[str, str],
    locations: ArtifactLocations,
) -> dict[str, Any]:
    return {
        "repository_id": repository_id(root),
        "head": head,
        "dirty": False,
        "scope": list(config.scope),
        "configuration_digest": configuration_digest(config),
        "engine": {key: value for key, value in engine.items() if key != "binary"},
        "artifact_dir": str(locations.directory),
        "manifest": str(locations.manifest),
        "lean_cache": str(locations.lean_cache),
        "rich_cache": str(locations.rich_cache),
    }


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(type(item) is not str or not item for item in value):
        raise ValueError(f"{field} must be a non-empty array of strings")
    return tuple(value)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
