"""Curated on-demand Ripwire skill profiles for the PR coordinator.

The upstream Ripwire skills are useful workflow guidance, but they assume a
shell-capable agent.  This module keeps adoption host-owned: compact guidance is
selected deterministically and never grants tools by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RipwireSkillPolicy:
    """Host-controlled switch for curated skill guidance."""

    enabled: bool = False
    requested_profiles: tuple[str, ...] = ()
    workflow_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class RipwireSkillProfile:
    """Compact coordinator-specific profile derived from a Ripwire skill."""

    name: str
    upstream_skill: str
    trigger: str
    guidance: str
    permitted_tools: tuple[str, ...]
    disabled_capabilities: tuple[str, ...] = (
        "arbitrary shell",
        "filesystem writes",
        "test execution",
        "GitHub/MCP/editor integration",
        "cache preparation",
        "network operations",
    )


RIPWIRE_SKILL_PROFILES: dict[str, RipwireSkillProfile] = {
    "change_check": RipwireSkillProfile(
        name="change_check",
        upstream_skill="ripwire-change-check",
        trigger="PR context is ok and hunk-selected candidate symbols exist.",
        guidance=(
            "Use PR-context evidence as the review seed; expand only selected "
            "candidate symbols, keep blast radius lower-bound/candidate, and "
            "disclose ambiguity, truncation, and missing coverage."
        ),
        permitted_tools=("symbol_impact", "inspect_repository_evidence"),
    ),
    "write_tests": RipwireSkillProfile(
        name="write_tests",
        upstream_skill="ripwire-write-tests",
        trigger="No affected tests were found or coverage/test suggestions are requested.",
        guidance=(
            "Suggest candidate tests from changed symbols, callers, seams, and "
            "affected-test evidence; never claim executed coverage and keep "
            "execution_status as not_run."
        ),
        permitted_tools=("inspect_repository_evidence",),
    ),
    "find_bug": RipwireSkillProfile(
        name="find_bug",
        upstream_skill="ripwire-find-bug",
        trigger="The workflow includes bug, regression, error, symptom, or trace context.",
        guidance=(
            "Treat symptoms as localization hints; connect reported behavior to "
            "changed symbols through candidate callers and bounded inspection."
        ),
        permitted_tools=("symbol_impact", "inspect_repository_evidence"),
    ),
    "orient": RipwireSkillProfile(
        name="orient",
        upstream_skill="ripwire-orient",
        trigger="PR context is thin, no symbols are selected, or subsystem orientation is requested.",
        guidance=(
            "Stay at map-before-read scale: summarize the smallest relevant "
            "subsystem context and choose one next evidence question."
        ),
        permitted_tools=("inspect_repository_evidence",),
    ),
    "graph_query": RipwireSkillProfile(
        name="graph_query",
        upstream_skill="ripwire-graph-query",
        trigger="A compound bounded caller/callee graph question is requested.",
        guidance=(
            "Use existing impact evidence first; graph-query remains design-only "
            "until a closed allowlisted graph tool exists."
        ),
        permitted_tools=(),
    ),
    "security_scan": RipwireSkillProfile(
        name="security_scan",
        upstream_skill="ripwire-security-scan",
        trigger="Skill/MCP adoption or security-sensitive PR triage is requested.",
        guidance=(
            "Use security guidance as structural triage only; do not claim taint "
            "proof, and scan external skill/MCP content before adoption."
        ),
        permitted_tools=("inspect_repository_evidence",),
    ),
}


_HINT_ROUTES = {
    "bug": "find_bug",
    "regression": "find_bug",
    "error": "find_bug",
    "symptom": "find_bug",
    "trace": "find_bug",
    "orient": "orient",
    "thin_context": "orient",
    "graph": "graph_query",
    "graph_query": "graph_query",
    "security": "security_scan",
    "skill": "security_scan",
    "mcp": "security_scan",
    "test": "write_tests",
    "tests": "write_tests",
    "coverage": "write_tests",
}


def agent_skills_supported() -> bool:
    """Return whether the pinned Strands runtime exposes AgentSkills."""

    try:
        from strands import AgentSkills  # noqa: F401
    except ImportError:
        return False
    return True


def select_ripwire_skill_profiles(
    seed: Any,
    policy: RipwireSkillPolicy | None = None,
) -> tuple[RipwireSkillProfile, ...]:
    """Select the minimal curated profiles for this host-controlled workflow."""

    if policy is None or not policy.enabled:
        return ()
    selected: set[str] = set()
    for name in policy.requested_profiles:
        if name not in RIPWIRE_SKILL_PROFILES:
            raise ValueError(f"unknown Ripwire skill profile: {name}")
        selected.add(name)

    context = seed.context
    changed_files = tuple(getattr(context, "changed_files", ()) or ())
    if getattr(seed, "allowed_symbols", ()):
        selected.add("change_check")
    if changed_files and all(not getattr(changed, "affected_tests", ()) for changed in changed_files):
        selected.add("write_tests")
    if changed_files and not getattr(seed, "allowed_symbols", ()):
        selected.add("orient")

    for hint in policy.workflow_hints:
        profile = _HINT_ROUTES.get(hint.strip().lower())
        if profile:
            selected.add(profile)

    return tuple(RIPWIRE_SKILL_PROFILES[name] for name in sorted(selected))


def render_ripwire_skill_guidance(profiles: tuple[RipwireSkillProfile, ...]) -> str:
    """Render compact skill guidance for the coordinator prompt."""

    if not profiles:
        return ""
    lines = ["Curated Ripwire skill profiles are active as guidance only; they grant no tools."]
    for profile in profiles:
        tools = ", ".join(profile.permitted_tools) if profile.permitted_tools else "none"
        lines.append(
            f"- {profile.name} ({profile.upstream_skill}): {profile.guidance} "
            f"Permitted coordinator tools: {tools}."
        )
    lines.append(
        "Disabled for all profiles: arbitrary shell, writes, test execution, "
        "cache preparation, network, GitHub, MCP, and editor integration."
    )
    return "\n".join(lines)


__all__ = [
    "RIPWIRE_SKILL_PROFILES",
    "RipwireSkillPolicy",
    "RipwireSkillProfile",
    "agent_skills_supported",
    "render_ripwire_skill_guidance",
    "select_ripwire_skill_profiles",
]
