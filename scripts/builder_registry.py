"""Declarative builder dependency planning for workspace repository refreshes.

The registry deliberately owns ordering.  Repository manifests may select
builders, but cannot accidentally run a dependent builder against stale input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Builder:
    name: str
    dependencies: tuple[str, ...] = ()
    profiles: frozenset[str] = frozenset({"generic", "intacct_app", "rest_automation"})


BUILDERS: dict[str, Builder] = {
    "scan": Builder("scan"),
    "symbols": Builder("symbols", ("scan",)),
    "relationships": Builder("relationships", ("symbols",)),
    "entities": Builder("entities", ("scan",), frozenset({"intacct_app"})),
    "entity_roots": Builder(
        "entity_roots", ("entities", "symbols"), frozenset({"intacct_app"})
    ),
    "openapi_scan": Builder("openapi_scan", ("scan",), frozenset({"intacct_app"})),
    "openapi_link": Builder(
        "openapi_link", ("openapi_scan", "entities"), frozenset({"intacct_app"})
    ),
    "workflows": Builder(
        "workflows", ("entity_roots", "openapi_scan"), frozenset({"intacct_app"})
    ),
    "security": Builder("security", ("scan",), frozenset({"intacct_app"})),
    "rest_endpoints": Builder(
        "rest_endpoints", ("openapi_link",), frozenset({"intacct_app"})
    ),
    "entity_access_links": Builder(
        "entity_access_links",
        ("workflows", "security", "rest_endpoints"),
        frozenset({"intacct_app"}),
    ),
    "integration_links": Builder("integration_links", ("relationships",)),
    "gherkin_coverage": Builder(
        "gherkin_coverage", ("integration_links",), frozenset({"rest_automation"})
    ),
}

PROFILE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "generic": ("scan", "symbols", "relationships", "integration_links"),
    "rest_automation": (
        "scan",
        "symbols",
        "relationships",
        "integration_links",
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
        "workflows",
        "security",
        "rest_endpoints",
        "entity_access_links",
        "integration_links",
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
