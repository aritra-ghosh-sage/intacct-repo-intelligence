"""Read-only, revision-bound Ripwire PR-context seed extraction."""

from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import (
    PrChangedFile,
    PrContextGap,
    PrContextRequest,
    PrContextResult,
    PrSymbolCandidate,
)
from .engines import _ripwire_binary
from .identity import git_revision, is_dirty
from .readiness import check_repomap_readiness, load_repomap_config


@dataclass(frozen=True)
class _GitChange:
    path: str
    change: str
    old_path: str | None = None


_HUNK_HEADER = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@"
)


def build_pr_context(request: PrContextRequest) -> PrContextResult:
    """Return seeds for the checked-out PR head against an explicit base ref.

    The exact clean checkout ``HEAD`` represents the PR head. A GitHub PR
    number is not resolved here; callers must provide the checkout and base
    reference directly, and a matching external index must already exist.
    """

    if request.token_budget is not None and request.token_budget <= 0:
        return PrContextResult(status="error", diagnostics=["token_budget must be positive when provided"])
    if request.limit <= 0:
        return PrContextResult(status="error", diagnostics=["limit must be positive"])
    if request.offset < 0:
        return PrContextResult(status="error", diagnostics=["offset must not be negative"])
    if request.history_commits <= 0:
        return PrContextResult(status="error", diagnostics=["history_commits must be positive"])

    readiness = check_repomap_readiness(request)
    if readiness.status != "ok":
        return PrContextResult(
            status=readiness.status,
            diagnostics=readiness.diagnostics,
            metrics=readiness.metrics,
            identity=readiness.identity,
        )

    root = request.repo_root.resolve()
    try:
        config = load_repomap_config(root)
        changes = _git_changes(root, readiness.identity["merge_base"])
    except ValueError as exc:
        return PrContextResult(status="error", diagnostics=[str(exc)], identity=readiness.identity)

    in_scope, gaps = _in_scope_changes(changes, config.scope)
    hunk_ranges: dict[str, tuple[tuple[int, int], ...]] = {}
    for change in in_scope:
        if change.change not in {"A", "M", "R"}:
            continue
        try:
            hunk_ranges[change.path] = _hunk_line_ranges(
                root,
                readiness.identity["merge_base"],
                readiness.identity["head"],
                change.path,
            )
        except ValueError as exc:
            gaps.append(
                PrContextGap(
                    kind="hunk_range_unavailable",
                    detail=f"Git hunk ranges are unavailable for {change.path}: {exc}",
                )
            )
    binary = _ripwire_binary()
    if binary is None:
        return PrContextResult(
            status="unavailable",
            diagnostics=["Ripwire binary became unavailable after readiness check"],
            gaps=gaps,
            identity=readiness.identity,
        )
    budget = request.token_budget if request.token_budget is not None else config.token_budget
    command = [
        binary,
        str(root / config.scope[0]),
        f"--cache={readiness.identity['lean_cache']}",
        f"--pr-context={request.base_ref}",
        "--legend=compact",
        f"--token-budget={budget}",
        f"--limit={request.limit}",
        f"--offset={request.offset}",
        f"--pr-history-commits={request.history_commits}",
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
        return PrContextResult(
            status="error",
            gaps=gaps,
            diagnostics=[f"Ripwire PR-context invocation failed: {exc}"],
            identity=readiness.identity,
        )
    if completed.returncode != 0:
        return PrContextResult(
            status="error",
            gaps=gaps,
            diagnostics=[f"Ripwire PR-context exited {completed.returncode}: {completed.stderr.strip()[:500]}"],
            identity=readiness.identity,
        )
    try:
        changed_files, xml_gaps, xml_metrics = _parse_pr_context_xml(
            root,
            config.scope[0],
            completed.stdout,
            in_scope,
            hunk_ranges=hunk_ranges,
        )
    except ValueError as exc:
        return PrContextResult(
            status="error",
            gaps=gaps,
            diagnostics=[str(exc)],
            identity=readiness.identity,
        )

    post_head = git_revision(root)
    post_dirty = is_dirty(root)
    if post_head != readiness.identity["head"] or post_dirty is not False:
        identity = dict(readiness.identity)
        identity.update({"post_run_head": post_head, "post_run_dirty": post_dirty})
        return PrContextResult(
            status="error",
            diagnostics=["repository checkout changed while PR context was being generated"],
            identity=identity,
        )

    metrics = {
        **xml_metrics,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "git_changed_files": len(changes),
        "in_scope_changed_files": len(in_scope),
    }
    return PrContextResult(
        status="ok",
        changed_files=changed_files,
        raw_xml=completed.stdout,
        gaps=[*gaps, *xml_gaps],
        metrics=metrics,
        identity=readiness.identity,
    )


def _git_changes(root: Path, merge_base: str) -> list[_GitChange]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-status", "-z", "--find-renames", merge_base, "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot read Git changed-file set: {exc}") from exc
    tokens = completed.stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    changes: list[_GitChange] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status:
            continue
        change = status[0]
        if change in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise ValueError(f"malformed Git {change} change record")
            old_path, path = tokens[index], tokens[index + 1]
            index += 2
            changes.append(_GitChange(path=path, change=change, old_path=old_path))
        else:
            if index >= len(tokens):
                raise ValueError(f"malformed Git {change} change record")
            changes.append(_GitChange(path=tokens[index], change=change))
            index += 1
    return sorted(changes, key=lambda item: (item.path, item.old_path or "", item.change))


def _hunk_line_ranges(
    root: Path,
    merge_base: str,
    head: str,
    path: str,
) -> tuple[tuple[int, int], ...]:
    """Return inclusive new-file line ranges from a zero-context Git diff."""

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "-U0",
                merge_base,
                head,
                "--",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot read Git hunks: {exc}") from exc

    ranges: list[tuple[int, int]] = []
    for line in completed.stdout.splitlines():
        match = _HUNK_HEADER.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count > 0:
            ranges.append((start, start + count - 1))
    return tuple(ranges)


def _in_scope_changes(
    changes: list[_GitChange], scope: tuple[str, ...]
) -> tuple[list[_GitChange], list[PrContextGap]]:
    prefixes = tuple(f"{item.rstrip('/')}/" for item in scope)
    selected: list[_GitChange] = []
    outside = 0
    unsupported = 0
    for change in changes:
        if change.change not in {"A", "M", "D", "R"}:
            unsupported += 1
            continue
        if any(change.path.startswith(prefix) for prefix in prefixes):
            selected.append(change)
        else:
            outside += 1
    gaps: list[PrContextGap] = []
    if outside:
        gaps.append(
            PrContextGap(
                kind="out_of_scope_changes",
                detail="Git changes outside configured repository-map scope were not indexed",
                count=outside,
            )
        )
    if unsupported:
        gaps.append(
            PrContextGap(
                kind="unsupported_git_change",
                detail="Git changes with unsupported status codes were not normalized",
                count=unsupported,
            )
        )
    return selected, gaps


def _parse_pr_context_xml(
    repo_root: Path,
    scope: str,
    output: str,
    changes: list[_GitChange],
    *,
    hunk_ranges: Mapping[str, Sequence[tuple[int, int]]] | None = None,
) -> tuple[list[PrChangedFile], list[PrContextGap], dict[str, int | str]]:
    try:
        root = ET.fromstring(output)
    except ET.ParseError as exc:
        raise ValueError(f"Ripwire PR-context XML parse failed: {exc}") from exc
    if root.tag != "pr-context" or root.attrib.get("schema") != "ripwire.pr-context/v1":
        raise ValueError("Ripwire output is not a ripwire.pr-context/v1 XML document")

    symbols_by_path: dict[str, tuple[PrSymbolCandidate, ...]] = {}
    xml_paths: set[str] = set()
    for file_node in root.findall("./file"):
        raw_path = file_node.attrib.get("p")
        if not raw_path:
            continue
        path = _normalize_scope_path(repo_root, scope, raw_path)
        if path is None:
            continue
        xml_paths.add(path)
        symbols: list[PrSymbolCandidate] = []
        for symbol in file_node.findall("./changed-symbols/s"):
            name = symbol.attrib.get("n")
            if not name:
                continue
            symbols.append(
                PrSymbolCandidate(
                    path=path,
                    name=name,
                    line=_symbol_line(symbol.attrib.get("p")),
                    kind=symbol.attrib.get("t"),
                )
            )
        symbols_by_path[path] = tuple(sorted(symbols, key=lambda item: (item.line or 0, item.name)))

    changed_files: list[PrChangedFile] = []
    gaps = _root_gaps(root)
    missing = 0
    symbols_before = 0
    symbols_after = 0
    hunks_total = 0
    hunks_unresolved = 0
    for change in changes:
        symbols = symbols_by_path.get(change.path, ())
        symbols_before += len(symbols)
        if change.change != "D" and change.path not in xml_paths:
            missing += 1
        if change.change == "D":
            symbols = ()
        elif hunk_ranges is not None and change.path in hunk_ranges:
            ranges = tuple(hunk_ranges[change.path])
            hunks_total += len(ranges)
            if not ranges:
                symbols = ()
                gaps.append(
                    PrContextGap(
                        kind="hunk_no_head_lines",
                        detail=(
                            "Git reported no changed lines in the current version of "
                            f"{change.path}"
                        ),
                        count=1,
                    )
                )
            else:
                symbols, missing_lines, unresolved = _select_hunk_symbols(symbols, ranges)
                hunks_unresolved += unresolved
                if missing_lines:
                    gaps.append(
                        PrContextGap(
                            kind="hunk_symbol_line_unavailable",
                            detail=(
                                "Ripwire symbols without line numbers could not be "
                                f"attributed in {change.path}"
                            ),
                            count=missing_lines,
                        )
                    )
                if unresolved:
                    gaps.append(
                        PrContextGap(
                            kind="hunk_symbol_unresolved",
                            detail=f"Changed hunks could not be attributed to a symbol in {change.path}",
                            count=unresolved,
                        )
                    )
        symbols_after += len(symbols)
        changed_files.append(
            PrChangedFile(
                path=change.path,
                change=change.change,
                old_path=change.old_path,
                symbols=symbols,
            )
        )

    changed_paths = {change.path for change in changes}
    unmatched = len(xml_paths - changed_paths)
    if missing:
        gaps.append(
            PrContextGap(
                kind="ripwire_missing_changed_file",
                detail="Ripwire did not return a changed-file row for Git changes in scope",
                count=missing,
            )
        )
    if unmatched:
        gaps.append(
            PrContextGap(
                kind="ripwire_unmatched_file",
                detail="Ripwire returned changed-file rows absent from the authoritative Git diff",
                count=unmatched,
            )
        )
    metrics: dict[str, int | str] = {}
    for name in (
        "files", "shown", "total", "capped", "next_offset", "budget_tokens",
        "est_tokens", "trim_level", "history_scope", "history_commits",
    ):
        value = root.attrib.get(name)
        if value is not None:
            metrics[name] = int(value) if value.isdigit() else value
    if hunk_ranges is not None:
        metrics.update(
            {
                "symbol_selection": "hunk-enclosing-v1",
                "candidate_symbols_before": symbols_before,
                "candidate_symbols_after": symbols_after,
                "hunks_total": hunks_total,
                "hunks_unresolved": hunks_unresolved,
            }
        )
    return changed_files, gaps, metrics


def _select_hunk_symbols(
    symbols: Sequence[PrSymbolCandidate],
    ranges: Sequence[tuple[int, int]],
) -> tuple[tuple[PrSymbolCandidate, ...], int, int]:
    """Select candidate definitions whose inferred spans overlap Git hunks."""

    ordered = sorted(
        (symbol for symbol in symbols if symbol.line is not None),
        key=lambda item: (item.line or 0, item.kind or "", item.name),
    )
    missing_lines = len(symbols) - len(ordered)
    groups: list[tuple[int, list[PrSymbolCandidate]]] = []
    for symbol in ordered:
        if groups and groups[-1][0] == symbol.line:
            groups[-1][1].append(symbol)
        else:
            groups.append((symbol.line or 0, [symbol]))

    selected: list[PrSymbolCandidate] = []
    matched_hunks: set[int] = set()
    last_hunk_line = max((end for _, end in ranges), default=0)
    for index, (start, group) in enumerate(groups):
        end = groups[index + 1][0] - 1 if index + 1 < len(groups) else last_hunk_line
        matching = {
            hunk_index
            for hunk_index, (hunk_start, hunk_end) in enumerate(ranges)
            if start <= hunk_end and hunk_start <= end
        }
        if matching:
            selected.extend(group)
            matched_hunks.update(matching)

    selected.sort(key=lambda item: (item.line or 0, item.kind or "", item.name))
    return tuple(selected), missing_lines, len(ranges) - len(matched_hunks)


def _normalize_scope_path(repo_root: Path, scope: str, raw_path: str) -> str | None:
    raw = Path(raw_path)
    if raw.is_absolute():
        try:
            return raw.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return None
    prefix = scope.rstrip("/")
    if raw_path == prefix or raw_path.startswith(f"{prefix}/"):
        return raw_path
    return f"{prefix}/{raw_path.lstrip('./')}"


def _symbol_line(value: str | None) -> int | None:
    if not value:
        return None
    _, separator, suffix = value.rpartition(":")
    return int(suffix) if separator and suffix.isdigit() else None


def _root_gaps(root: ET.Element) -> list[PrContextGap]:
    gaps: list[PrContextGap] = []
    history_scope = root.attrib.get("history_scope")
    history_commits = _as_int(root.attrib.get("history_commits"))
    if history_scope == "head-count" and history_commits is not None and history_commits > 0:
        gaps.append(
            PrContextGap(
                "bounded_history",
                f"Ripwire limited co-change and ownership history to the latest {history_commits} commits",
                history_commits,
            )
        )
    truncated = root.attrib.get("truncated")
    if truncated and truncated not in {"none", "0", "false"}:
        gaps.append(PrContextGap("truncated", f"Ripwire truncated output at level: {truncated}"))
    for attribute, kind in (("graph_ambiguous", "graph_ambiguous"), ("graph_unresolved", "graph_unresolved")):
        value = root.attrib.get(attribute, "0")
        if value.isdigit() and int(value) > 0:
            gaps.append(PrContextGap(kind, f"Ripwire reported {attribute}={value}", int(value)))
    if root.attrib.get("capped") in {"1", "true"} or root.attrib.get("has_more") in {"1", "true"}:
        gaps.append(
            PrContextGap(
                "pagination",
                "Ripwire returned a partial changed-file window; use next_offset to continue",
                _as_int(root.attrib.get("next_offset")),
            )
        )
    return gaps


def _as_int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None
