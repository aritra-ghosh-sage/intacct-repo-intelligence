"""On-demand symbol-scoped Ripwire impact evidence."""

from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import (
    PrContextGap,
    PrImpactCandidate,
    PrImpactRequest,
    PrImpactResult,
    PrepareRepoMapRequest,
)
from .engines import _ripwire_binary
from .identity import git_revision, is_dirty
from .pr_context import _normalize_scope_path, _path_in_scope
from .readiness import check_prepared_repomap_readiness, load_repomap_config


def build_symbol_impact(request: PrImpactRequest) -> PrImpactResult:
    """Return lower-bound transitive impact evidence for one indexed symbol."""

    validation = _validate_request(request)
    if validation:
        return PrImpactResult(status="error", diagnostics=[validation])

    readiness = check_prepared_repomap_readiness(
        PrepareRepoMapRequest(request.repo_root, request.artifact_root)
    )
    if readiness.status != "ok":
        return PrImpactResult(
            status=readiness.status,
            diagnostics=readiness.diagnostics,
            identity=readiness.identity,
        )

    root = request.repo_root.resolve()
    try:
        config = load_repomap_config(root)
    except (OSError, ValueError) as exc:
        return PrImpactResult(status="error", diagnostics=[str(exc)], identity=readiness.identity)
    scope = config.scope[0].rstrip("/")
    if not _path_in_scope(request.symbol_path, scope) or request.symbol_path.rstrip("/") == scope:
        return PrImpactResult(
            status="error",
            diagnostics=[f"symbol_path must be within configured scope: {config.scope[0]}"],
            identity=readiness.identity,
        )
    relative_symbol_path = request.symbol_path[len(scope) + 1 :]
    binary = _ripwire_binary()
    if binary is None:
        return PrImpactResult(
            status="unavailable",
            diagnostics=["Ripwire binary became unavailable after readiness check"],
            identity=readiness.identity,
        )
    command = [
        binary,
        str(root / config.scope[0]),
        f"--cache={readiness.identity['lean_cache']}",
        f"--impact={relative_symbol_path}:{request.symbol_name}",
        f"--limit={request.limit}",
        f"--offset={request.offset}",
        "--legend=compact",
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return PrImpactResult(
            status="error",
            diagnostics=[f"Ripwire impact invocation failed: {exc}"],
            identity=readiness.identity,
        )
    raw_xml = completed.stdout
    if completed.returncode != 0:
        if _is_symbol_resolution_refusal(completed.stderr):
            return PrImpactResult(
                status="unavailable",
                raw_xml=raw_xml,
                gaps=[
                    PrContextGap(
                        kind="impact_symbol_unresolved",
                        detail=completed.stderr.strip()[:500] or "Ripwire could not resolve the requested symbol",
                    )
                ],
                diagnostics=[completed.stderr.strip()[:500] or f"Ripwire impact exited {completed.returncode}"],
                metrics={"elapsed_ms": _elapsed_ms(started)},
                identity=readiness.identity,
            )
        return PrImpactResult(
            status="error",
            raw_xml=raw_xml,
            diagnostics=[f"Ripwire impact exited {completed.returncode}: {completed.stderr.strip()[:500]}"],
            metrics={"elapsed_ms": _elapsed_ms(started)},
            identity=readiness.identity,
        )

    try:
        candidates, gaps, metrics = _parse_impact_xml(root, config.scope[0], raw_xml)
    except ValueError as exc:
        return PrImpactResult(
            status="error",
            raw_xml=raw_xml,
            diagnostics=[str(exc)],
            metrics={"elapsed_ms": _elapsed_ms(started)},
            identity=readiness.identity,
        )

    after_head = git_revision(root)
    after_dirty = is_dirty(root)
    if after_head != readiness.identity.get("head") or after_dirty is not False:
        return PrImpactResult(
            status="error",
            raw_xml=raw_xml,
            diagnostics=["repository revision or clean-tree state changed during Ripwire impact analysis"],
            metrics={"elapsed_ms": _elapsed_ms(started)},
            identity=readiness.identity,
        )
    metrics["elapsed_ms"] = _elapsed_ms(started)
    identity = dict(readiness.identity)
    identity.update({"symbol_path": request.symbol_path, "symbol_name": request.symbol_name})
    return PrImpactResult(
        status="ok",
        candidates=candidates,
        raw_xml=raw_xml,
        gaps=gaps,
        diagnostics=readiness.diagnostics,
        metrics=metrics,
        identity=identity,
    )


def _validate_request(request: PrImpactRequest) -> str | None:
    if not request.symbol_path or Path(request.symbol_path).is_absolute():
        return "symbol_path must be a non-empty repository-relative path"
    if ".." in Path(request.symbol_path).parts:
        return "symbol_path must not contain path traversal"
    if not request.symbol_name.strip():
        return "symbol_name must not be empty"
    if request.limit <= 0:
        return "limit must be positive"
    if request.offset < 0:
        return "offset must not be negative"
    return None


def _parse_impact_xml(
    repo_root: Path,
    scope: str,
    output: str,
) -> tuple[list[PrImpactCandidate], list[PrContextGap], dict[str, int | str]]:
    try:
        root = ET.fromstring(output)
    except ET.ParseError as exc:
        raise ValueError(f"Ripwire impact XML parse failed: {exc}") from exc
    if root.tag != "impact" or root.attrib.get("schema") != "ripwire.impact/v1":
        raise ValueError("Ripwire output is not a ripwire.impact/v1 XML document")

    candidates: list[PrImpactCandidate] = []
    missing_location = 0
    out_of_scope = 0
    for symbol in root.findall("./s"):
        name = symbol.attrib.get("n")
        raw_path, line = _split_location(symbol.attrib.get("p"))
        if not name or raw_path is None or line is None or line <= 0:
            missing_location += 1
            continue
        if not Path(raw_path).is_absolute() and ".." in Path(raw_path).parts:
            out_of_scope += 1
            continue
        path = _normalize_scope_path(repo_root, scope, raw_path)
        if path is None or not _path_in_scope(path, scope):
            out_of_scope += 1
            continue
        candidates.append(
            PrImpactCandidate(path=path, name=name, line=line, kind=symbol.attrib.get("t"))
        )
    unique = {
        (item.path, item.name, item.line, item.kind, item.confidence): item
        for item in candidates
    }
    ordered = sorted(unique.values(), key=lambda item: (item.path, item.line, item.kind or "", item.name))

    metrics: dict[str, int | str] = {}
    for name in (
        "defs", "reaches", "shown", "total", "capped", "has_more", "next_offset",
        "offset", "limit", "radius_tested", "radius_untested", "importers",
        "shown_importers", "importers_capped", "graph_ambiguous", "graph_unresolved",
    ):
        value = root.attrib.get(name)
        if value is not None:
            metrics[name] = int(value) if _is_integer(value) else value
    gaps: list[PrContextGap] = [
        PrContextGap(
            kind="impact_lower_bound",
            detail="Ripwire impact counts and rows are static-analysis floors, not exhaustive totals",
        )
    ]
    if missing_location:
        gaps.append(
            PrContextGap(
                kind="impact_location_unavailable",
                detail="Impact rows lacked a usable path and positive line",
                count=missing_location,
            )
        )
    if out_of_scope:
        gaps.append(
            PrContextGap(
                kind="impact_out_of_scope",
                detail="Impact rows were outside the configured scope",
                count=out_of_scope,
            )
        )
    if _truthy(root.attrib.get("capped")) or _truthy(root.attrib.get("has_more")):
        gaps.append(
            PrContextGap(
                kind="impact_truncated",
                detail="Ripwire impact rows were capped or paginated",
                count=_int_attr(root, "total") or None,
            )
        )
    if _int_attr(root, "importers") or _int_attr(root, "shown_importers"):
        gaps.append(
            PrContextGap(
                kind="impact_importers_not_normalized",
                detail="Import reach rows remain canonical XML evidence in this slice",
                count=_int_attr(root, "importers") or _int_attr(root, "shown_importers"),
            )
        )
    if _int_attr(root, "graph_ambiguous"):
        gaps.append(
            PrContextGap(
                kind="graph_ambiguous",
                detail="Ripwire reported ambiguous impact edges",
                count=_int_attr(root, "graph_ambiguous"),
            )
        )
    if _int_attr(root, "graph_unresolved"):
        gaps.append(
            PrContextGap(
                kind="graph_unresolved",
                detail="Ripwire reported unresolved impact edges",
                count=_int_attr(root, "graph_unresolved"),
            )
        )
    return ordered, gaps, metrics


def _split_location(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    path, separator, suffix = value.rpartition(":")
    if not separator or not path or not suffix.isdigit():
        return None, None
    return path, int(suffix)


def _is_symbol_resolution_refusal(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "symbol not found" in lowered
        or "symbol is ambiguous" in lowered
        or "symbol ambiguous" in lowered
        or "ambiguous symbol" in lowered
    )


def _is_integer(value: str) -> bool:
    return value.isdigit() or (value.startswith("-") and value[1:].isdigit())


def _int_attr(root: ET.Element, name: str) -> int:
    value = root.attrib.get(name)
    return int(value) if value is not None and _is_integer(value) else 0


def _truthy(value: str | None) -> bool:
    return value in {"1", "true", "yes"}


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
