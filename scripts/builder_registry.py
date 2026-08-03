"""Declarative builder dependency planning for workspace repository refreshes.

The registry deliberately owns ordering.  Repository manifests may select
builders, but cannot accidentally run a dependent builder against stale input.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from catalog.delta import path_is_in_scan_scope
from scripts.scan_ent_files import is_entity_input_path

SourceMatcher = Callable[[str], bool]


def _any(_path: str) -> bool:
    return True


def _none(_path: str) -> bool:
    return False


def _suffix(*suffixes: str) -> SourceMatcher:
    allowed = frozenset(value.lower() for value in suffixes)
    return lambda path: PurePosixPath(path).suffix.lower() in allowed


def _contains(*parts: str) -> SourceMatcher:
    lowered = tuple(part.lower() for part in parts)
    return lambda path: any(part in path.lower() for part in lowered)


def _exact(*paths: str) -> SourceMatcher:
    allowed = frozenset(PurePosixPath(path).as_posix().lower() for path in paths)
    return lambda path: PurePosixPath(path).as_posix().lower() in allowed


def _or(*matchers: SourceMatcher) -> SourceMatcher:
    return lambda path: any(matcher(path) for matcher in matchers)


def _ui_surface_input(path: str) -> bool:
    """Match only source changes that can alter persisted UI evidence."""
    normalized = PurePosixPath(path).as_posix().lower()
    source_path = normalized.startswith("app/source/")
    resource_path = normalized.startswith("app/resources/")
    name = PurePosixPath(normalized).name
    suffix = PurePosixPath(normalized).suffix
    if source_path and name.endswith("_form.xml"):
        return True
    if source_path and suffix in {".php", ".cls", ".inc", ".ent"}:
        return True
    if resource_path and suffix == ".js":
        return True
    return normalized.startswith("app/source/openapispec/") and suffix in {
        ".yaml",
        ".yml",
        ".json",
    }


@dataclass(frozen=True)
class Builder:
    name: str
    dependencies: tuple[str, ...] = ()
    profiles: frozenset[str] = frozenset({"generic", "intacct_app", "rest_automation"})
    delta_capability: str = "scoped_full"
    source_matcher: SourceMatcher = _any
    invalidates: tuple[str, ...] = ()


BUILDERS: dict[str, Builder] = {
    "scan": Builder(
        "scan", delta_capability="exact", source_matcher=path_is_in_scan_scope
    ),
    "symbols": Builder(
        "symbols",
        ("scan",),
        delta_capability="exact",
        source_matcher=path_is_in_scan_scope,
        invalidates=(
            "workflows",
            "entity_semantics",
            "entity_access_links",
        ),
    ),
    "relationships": Builder(
        "relationships",
        ("symbols",),
        delta_capability="exact",
        source_matcher=path_is_in_scan_scope,
    ),
    "entities": Builder(
        "entities",
        ("scan",),
        frozenset({"intacct_app"}),
        source_matcher=is_entity_input_path,
        invalidates=(
            "entity_roots",
            "openapi_link",
            "workflows",
            "rest_endpoints",
            "entity_semantics",
            "entity_access_links",
        ),
    ),
    "entity_roots": Builder(
        "entity_roots",
        ("entities", "symbols"),
        frozenset({"intacct_app"}),
        source_matcher=is_entity_input_path,
    ),
    "openapi_scan": Builder(
        "openapi_scan",
        ("scan",),
        frozenset({"intacct_app"}),
        source_matcher=_contains("openapispec", "openapi"),
        invalidates=(
            "entities",
            "openapi_link",
            "workflows",
            "rest_endpoints",
            "entity_semantics",
            "entity_access_links",
        ),
    ),
    "openapi_link": Builder(
        "openapi_link",
        ("openapi_scan", "entities"),
        frozenset({"intacct_app"}),
        source_matcher=_or(_contains("openapispec", "openapi"), _suffix(".ent")),
        invalidates=(
            "workflows",
            "rest_endpoints",
            "entity_semantics",
            "entity_access_links",
        ),
    ),
    "ui_surfaces": Builder(
        "ui_surfaces",
        ("relationships", "entities", "openapi_link"),
        frozenset({"intacct_app"}),
        delta_capability="scoped_full",
        source_matcher=_ui_surface_input,
    ),
    "workflows": Builder(
        "workflows",
        ("entity_roots", "openapi_scan"),
        frozenset({"intacct_app"}),
        source_matcher=_or(_contains("workflow", "allowedoperations"), _suffix(".ent")),
        invalidates=("entity_access_links",),
    ),
    "security": Builder(
        "security",
        ("scan",),
        frozenset({"intacct_app"}),
        source_matcher=_or(
            _contains("security", "permission", "menu"),
            _suffix(".pol", ".menu"),
            _exact("app/source/common/dbschema.inc"),
        ),
        invalidates=("entity_semantics", "entity_access_links"),
    ),
    "rest_endpoints": Builder(
        "rest_endpoints",
        ("openapi_link",),
        frozenset({"intacct_app"}),
        source_matcher=_or(
            _contains("openapispec", "openapi", "registr"), _suffix(".ent")
        ),
        invalidates=("entity_semantics", "entity_access_links", "gherkin_coverage"),
    ),
    "entity_semantics": Builder(
        "entity_semantics",
        ("entities", "openapi_link", "security", "rest_endpoints"),
        frozenset({"intacct_app"}),
        source_matcher=_or(
            _suffix(".ent"), _contains("openapispec", "security", "permission")
        ),
        invalidates=("entity_access_links",),
    ),
    "entity_access_links": Builder(
        "entity_access_links",
        ("workflows", "security", "rest_endpoints", "entity_semantics"),
        frozenset({"intacct_app"}),
        source_matcher=_none,
    ),
    "integration_links": Builder(
        "integration_links",
        ("relationships",),
        delta_capability="unsupported",
        source_matcher=_any,
    ),
    "gherkin_coverage": Builder(
        "gherkin_coverage",
        ("scan",),
        frozenset({"rest_automation"}),
        source_matcher=_none,
    ),
}

PROFILE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "generic": ("scan", "symbols", "relationships"),
    "rest_automation": (
        "scan",
        "symbols",
        "relationships",
        "gherkin_coverage",
    ),
    "intacct_app": (
        "scan",
        "symbols",
        "relationships",
        "entities",
        "entity_roots",
        "openapi_scan",
        "openapi_link",
        "ui_surfaces",
        "workflows",
        "security",
        "rest_endpoints",
        "entity_semantics",
        "entity_access_links",
    ),
}


class BuilderPlanError(ValueError):
    """A repository selected an unknown or incompatible builder."""


def build_plan(profile: str, requested: Iterable[str] | None = None) -> list[str]:
    """Return a deterministic topological builder plan.

    `requested` is additive to a profile default.  Dependency expansion is
    recursive and profile compatibility is checked for both requested builders
    and their prerequisites.
    """
    if profile not in PROFILE_DEFAULTS:
        raise BuilderPlanError(f"unknown repository profile: {profile}")

    selected = list(PROFILE_DEFAULTS[profile])
    if requested is not None:
        selected.extend(str(value) for value in requested)

    ordered: list[str] = []
    permanent: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        builder = BUILDERS.get(name)
        if builder is None:
            raise BuilderPlanError(f"unknown builder: {name}")
        if profile not in builder.profiles:
            raise BuilderPlanError(
                f"builder {name!r} is not supported by profile {profile!r}"
            )
        if builder.delta_capability == "unsupported":
            raise BuilderPlanError(
                f"builder {name!r} is unsupported and has no deterministic runner"
            )
        if name in permanent:
            return
        if name in visiting:
            raise BuilderPlanError(f"cyclic builder dependency at {name!r}")
        visiting.add(name)
        for dependency in builder.dependencies:
            visit(dependency)
        visiting.remove(name)
        permanent.add(name)
        ordered.append(name)

    for name in selected:
        visit(name)
    return ordered


def invalidated_builders(
    plan: Iterable[str],
    changed_paths: Iterable[str],
    forced: Iterable[str] = (),
    matcher_overrides: dict[str, SourceMatcher] | None = None,
) -> dict[str, str]:
    """Return selected builders and concrete transitive invalidation reasons."""

    ordered = list(plan)
    selected = set(ordered)
    reasons: dict[str, str] = {}
    for name in ordered:
        matcher = (matcher_overrides or {}).get(name, BUILDERS[name].source_matcher)
        matches = sorted(path for path in changed_paths if matcher(path))
        if matches:
            reasons[name] = f"source change: {matches[0]}"
    for name in forced:
        if name in selected:
            reasons[name] = "cross-repository dependency invalidation"

    changed = True
    while changed:
        changed = False
        for source in ordered:
            if source not in reasons:
                continue
            for target in BUILDERS[source].invalidates:
                if target in selected and target not in reasons:
                    reasons[target] = f"invalidated by {source}"
                    changed = True
    return reasons


def stage_execution_modes(
    plan: Iterable[str],
    *,
    repository_mode: str,
    changed_paths: Iterable[str] = (),
    forced: Iterable[str] = (),
    matcher_overrides: dict[str, SourceMatcher] | None = None,
) -> dict[str, tuple[str, str]]:
    ordered = list(plan)
    if repository_mode == "full":
        return {name: ("full", "full repository refresh") for name in ordered}
    reasons = invalidated_builders(
        ordered, changed_paths, forced, matcher_overrides=matcher_overrides
    )
    result: dict[str, tuple[str, str]] = {}
    for name in ordered:
        reason = reasons.get(name)
        if reason is None:
            result[name] = ("skipped", "source inputs unchanged")
            continue
        capability = BUILDERS[name].delta_capability
        result[name] = ("delta" if capability == "exact" else "full", reason)
    return result


def repository_matcher_overrides(manifest_entry: dict) -> dict[str, SourceMatcher]:
    """Build exact operator-configured matchers without broad path heuristics."""

    if manifest_entry.get("profile") != "rest_automation":
        return {}
    settings = manifest_entry.get("rest_automation") or {}
    features_root = (
        PurePosixPath(str(settings.get("features_root", ""))).as_posix().rstrip("/")
    )
    object_mapping = PurePosixPath(str(settings.get("object_mapping", ""))).as_posix()

    def gherkin(path: str) -> bool:
        normalized = PurePosixPath(path).as_posix()
        under_features = bool(features_root) and normalized.startswith(
            features_root + "/"
        )
        return normalized == object_mapping or (
            under_features
            and PurePosixPath(normalized).suffix.lower() in {".feature", ".properties"}
        )

    return {"gherkin_coverage": gherkin}
