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
    PrAffectedTestCandidate,
    PrCallerCandidate,
    PrChangedElementCandidate,
    PrChangedFile,
    PrContextGap,
    PrContextRequest,
    PrContextResult,
    PrImpactedFileCandidate,
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


@dataclass(frozen=True)
class _CallerEvidence:
    unresolved: int = 0
    out_of_scope: int = 0
    declared: int | None = None
    shown: int | None = None
    capped: bool = False


@dataclass(frozen=True)
class _ElementSpan:
    candidate: PrChangedElementCandidate
    start_line: int
    end_line: int
    depth: int


_HUNK_HEADER = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@"
)
_ENT_SCHEMA_ASSIGNMENT = re.compile(
    r"\$kSchemas\s*\[\s*(['\"])(object|importOrder|schema)\1\s*\]"
    r"\s*=\s*(?:array\s*\(|\[)",
)
_PHP_ARRAY_OPEN = re.compile(r"array\s*\(|\[")
_YAML_PROPERTY_KEY = re.compile(r"^([ \t]*)(['\"]?)([A-Za-z0-9_.-]+)\2[ \t]*:[ \t]*(?:#.*)?$")
_YAML_MAPPING_LINE = re.compile(r"^( *)(?:'[^']+'|\"[^\"]+\"|[A-Za-z0-9_.$/-]+)[ \t]*:")
_YAML_SEQUENCE_LINE = re.compile(r"^( *)-(?:[ \t]+.*)?$")
_YAML_MAPPED_TO = re.compile(
    r"(?m)^[ \t]*x-mappedTo[ \t]*:[ \t]*(?:['\"]([^'\"]+)['\"]|([A-Za-z0-9_.-]+))[ \t]*(?:#.*)?$"
)
_GENERIC_OPENAPI_PROPERTIES = frozenset({"key", "id", "href"})
_PHP_ENTRY = re.compile(r"^[ \t]*(['\"])([A-Za-z0-9_.-]+)\1[ \t]*=>")
_PHP_STRING = re.compile(r"(['\"])([^'\"\r\n]{1,160})\1")


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
    inventory = tuple(_change_dict(change, config.scope) for change in changes)
    hunk_ranges: dict[str, tuple[tuple[int, int], ...]] = {}
    head_elements: dict[str, tuple[_ElementSpan, ...]] = {}
    for change in in_scope:
        if change.change == "D":
            gaps.append(PrContextGap(
                kind="hunk_no_head_lines",
                detail=f"Deleted file {change.path} has no positive-side lines for symbol attribution",
                count=1,
            ))
            continue
        if change.change not in {"A", "M", "R", "C"}:
            continue
        try:
            hunk_ranges[change.path] = _hunk_line_ranges(
                root,
                readiness.identity["merge_base"],
                readiness.identity["head"],
                change.path,
            )
            source = _git_head_file(root, readiness.identity["head"], change.path)
            if source is not None:
                head_elements[change.path] = _extract_changed_element_spans(change.path, source)
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
            changed_files=_host_changed_files(changes, config.scope, hunk_ranges, head_elements),
            gaps=gaps,
            identity=readiness.identity,
            git_inventory=inventory,
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
            changed_files=_host_changed_files(changes, config.scope, hunk_ranges, head_elements),
            gaps=gaps,
            diagnostics=[f"Ripwire PR-context invocation failed: {exc}"],
            identity=readiness.identity,
            git_inventory=inventory,
        )
    if completed.returncode != 0:
        return PrContextResult(
            status="error",
            changed_files=_host_changed_files(changes, config.scope, hunk_ranges, head_elements),
            gaps=gaps,
            diagnostics=[f"Ripwire PR-context exited {completed.returncode}: {completed.stderr.strip()[:500]}"],
            identity=readiness.identity,
            git_inventory=inventory,
        )
    try:
        parsed_files, xml_gaps, xml_metrics = _parse_pr_context_xml(
            root,
            config.scope[0],
            completed.stdout,
            in_scope,
            hunk_ranges=hunk_ranges,
            changed_elements_by_path=head_elements,
        )
    except ValueError as exc:
        return PrContextResult(
            status="error",
            changed_files=_host_changed_files(changes, config.scope, hunk_ranges, head_elements),
            gaps=gaps,
            diagnostics=[str(exc)],
            identity=readiness.identity,
            git_inventory=inventory,
        )

    parsed_by_path = {item.path: item for item in parsed_files}
    changed_files = []
    for change in changes:
        item = parsed_by_path.get(change.path)
        if item is None:
            item = PrChangedFile(
                path=change.path,
                change=change.change,
                old_path=change.old_path,
                scope="in_scope" if _path_in_scope(change.path, config.scope[0]) else "out_of_scope",
            )
        changed_files.append(item)

    post_head = git_revision(root)
    post_dirty = is_dirty(root)
    if post_head != readiness.identity["head"] or post_dirty is not False:
        identity = dict(readiness.identity)
        identity.update({"post_run_head": post_head, "post_run_dirty": post_dirty})
        return PrContextResult(
            status="error",
            diagnostics=["repository checkout changed while PR context was being generated"],
            identity=identity,
            git_inventory=inventory,
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
        git_inventory=inventory,
    )


def _change_dict(change: _GitChange, scope: tuple[str, ...]) -> dict[str, object]:
    return {
        "path": change.path,
        "change": change.change,
        "old_path": change.old_path,
        "scope": "in_scope" if any(_path_in_scope(change.path, item) for item in scope) else "out_of_scope",
    }


def _host_changed_files(
    changes: Sequence[_GitChange],
    scope: tuple[str, ...],
    hunk_ranges: Mapping[str, Sequence[tuple[int, int]]] | None = None,
    elements_by_path: Mapping[str, Sequence[_ElementSpan]] | None = None,
) -> list[PrChangedFile]:
    return [
        PrChangedFile(
            path=change.path,
            change=change.change,
            old_path=change.old_path,
            changed_elements=_select_hunk_elements(
                (elements_by_path or {}).get(change.path, ()),
                (hunk_ranges or {}).get(change.path, ()),
            ),
            scope="in_scope" if any(_path_in_scope(change.path, item) for item in scope) else "out_of_scope",
        )
        for change in changes
    ]


def _select_hunk_elements(
    elements: Sequence[_ElementSpan],
    ranges: Sequence[tuple[int, int]],
) -> tuple[PrChangedElementCandidate, ...]:
    selected: dict[tuple[str, int], PrChangedElementCandidate] = {}
    for start, end in ranges:
        matching = [
            element for element in elements
            if element.start_line <= end and start <= element.end_line
        ]
        if not matching:
            continue
        deepest = max(element.depth for element in matching)
        best = [element for element in matching if element.depth == deepest]
        if len(best) == 1:
            element = best[0]
            selected[(element.candidate.name, element.candidate.line)] = element.candidate
    return tuple(sorted(selected.values(), key=lambda item: (item.line, item.name, item.kind)))


def _git_changes(root: Path, merge_base: str) -> list[_GitChange]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-status", "-z", "--find-renames", "--find-copies", merge_base, "HEAD"],
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


def _git_head_file(root: Path, head: str, path: str) -> str | None:
    """Read a changed file from the verified revision, never from the worktree."""

    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "show", f"{head}:{path}"],
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def _extract_changed_element_spans(path: str, source: str) -> tuple[_ElementSpan, ...]:
    suffix = Path(path).suffix.lower()
    if suffix == ".ent":
        return _extract_ent_elements(path, source)
    if suffix in {".yaml", ".yml"}:
        return _extract_openapi_elements(path, source)
    return ()


def _extract_ent_elements(path: str, source: str) -> tuple[_ElementSpan, ...]:
    matches = list(_ENT_SCHEMA_ASSIGNMENT.finditer(source))
    section_counts: dict[str, int] = {}
    for match in matches:
        section_counts[match.group(2)] = section_counts.get(match.group(2), 0) + 1

    elements: list[_ElementSpan] = []
    for match in matches:
        section = match.group(2)
        if section_counts[section] != 1:
            continue
        openers = list(_PHP_ARRAY_OPEN.finditer(source, match.start(), match.end()))
        if not openers:
            continue
        opener = openers[-1]
        opening_index = opener.end() - 1
        close_index, entries = _scan_php_array(source, opening_index)
        if close_index is None or not entries or any(not key for key, _, _ in entries):
            continue
        for index, (key, start, value_start) in enumerate(entries):
            next_start = entries[index + 1][1] if index + 1 < len(entries) else close_index
            value_end = next_start
            while value_end > value_start and source[value_end - 1].isspace():
                value_end -= 1
            if value_end > value_start and source[value_end - 1] == ",":
                value_end -= 1
            while value_end > value_start and source[value_end - 1].isspace():
                value_end -= 1
            value = source[start:value_end]
            terms = _unique_bounded_terms([key, *(item.group(2) for item in _PHP_STRING.finditer(value))])
            if not terms:
                continue
            kind = "ent_schema_mapping" if section == "schema" else "ent_schema_member"
            name = f"{Path(path).stem}.{section}.{key}"
            elements.append(_ElementSpan(
                PrChangedElementCandidate(path, name, _line_number(source, start), kind, terms),
                _line_number(source, start),
                _line_number(source, value_end),
                1,
            ))
    return tuple(sorted(elements, key=lambda item: (item.start_line, item.candidate.name)))


def _scan_php_array(
    source: str, opening_index: int
) -> tuple[int | None, list[tuple[str, int, int]]]:
    """Find direct literal-key entries in one balanced PHP array expression."""

    opening = source[opening_index]
    closing_for = {"[": "]", "(": ")", "{": "}"}
    if opening not in closing_for:
        return None, []
    stack = [closing_for[opening]]
    quote: str | None = None
    block_comment = False
    line_comment = False
    entries: list[tuple[str, int, int]] = []
    line_start = source.rfind("\n", 0, opening_index) + 1
    index = opening_index + 1
    close_index: int | None = None
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if index == line_start and not quote and not block_comment and not line_comment and len(stack) == 1:
            line_end = source.find("\n", index, len(source))
            if line_end < 0:
                line_end = len(source)
            line_text = source[index:line_end]
            entry = _PHP_ENTRY.match(line_text)
            if entry is not None:
                key = entry.group(2)
                entries.append((key, index, index + entry.end()))
            else:
                stripped = line_text.strip()
                if stripped and not stripped.startswith(("//", "#", "/*", "*", ")", "]", ",")):
                    entries.append(("", index, index))
        if line_comment:
            if char == "\n":
                line_comment = False
        elif block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 1
        elif quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "/" and following == "*":
            block_comment = True
            index += 1
        elif (char == "/" and following == "/") or char == "#":
            line_comment = True
            if char == "/":
                index += 1
        elif char in closing_for:
            stack.append(closing_for[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                close_index = index
                break
        if char == "\n":
            line_start = index + 1
        index += 1
    return close_index, entries


def _extract_openapi_elements(path: str, source: str) -> tuple[_ElementSpan, ...]:
    lines = source.splitlines()
    if any(re.match(r"^\s*\t", line) for line in lines):
        return ()
    is_openapi = "openapi" in path.lower() or any(
        re.match(r"^(?:openapi|swagger)\s*:", line) for line in lines
    )
    if not is_openapi:
        return ()
    scalar_lines = _yaml_block_scalar_lines(lines)
    properties_headers: list[int] = []
    for index, line in enumerate(lines):
        if index not in scalar_lines and re.match(r"^[ \t]*properties[ \t]*:[ \t]*(?:#.*)?$", line):
            properties_headers.append(index)

    provisional: list[dict[str, object]] = []
    for header_index in properties_headers:
        parent_indent = len(lines[header_index]) - len(lines[header_index].lstrip(" \t"))
        schema_name = _openapi_schema_name(lines, header_index)
        section_end = len(lines)
        for index in range(header_index + 1, len(lines)):
            if index in scalar_lines or not lines[index].strip() or lines[index].lstrip().startswith("#"):
                continue
            indent = len(lines[index]) - len(lines[index].lstrip(" \t"))
            if indent <= parent_indent:
                section_end = index
                break
        children: list[tuple[int, int, str]] = []
        child_indent: int | None = None
        malformed = False
        for index in range(header_index + 1, section_end):
            line = lines[index]
            if index in scalar_lines or not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            if indent <= parent_indent:
                continue
            match = _YAML_PROPERTY_KEY.match(line)
            if child_indent is None:
                if match is None:
                    malformed = True
                    break
                child_indent = indent
            if indent == child_indent:
                if match is None:
                    malformed = True
                    break
                children.append((index, indent, match.group(3)))
        if malformed or not children:
            continue
        names = [name for _, _, name in children]
        if len(names) != len(set(names)):
            continue
        for child_index, indent, name in children:
            sibling = next((item[0] for item in children if item[0] > child_index), section_end)
            end_index = sibling - 1
            while end_index >= child_index and (not lines[end_index].strip() or lines[end_index].lstrip().startswith("#")):
                end_index -= 1
            block = "\n".join(lines[child_index:end_index + 1])
            if re.search(r"[{}]|(?:^|\s)[&*][A-Za-z0-9_-]+", block):
                continue
            if not _yaml_property_block_supported(lines, child_index, end_index, scalar_lines):
                continue
            provisional.append({
                "line_index": child_index,
                "end_index": end_index,
                "name": name,
                "indent": indent,
                "schema_name": schema_name,
            })

    provisional.sort(key=lambda item: int(item["line_index"]))
    elements: list[_ElementSpan] = []
    stem = Path(path).stem
    for item in provisional:
        line_index = int(item["line_index"])
        end_index = int(item["end_index"])
        enclosing = [
            parent for parent in provisional
            if int(parent["line_index"]) < line_index <= int(parent["end_index"])
        ]
        enclosing.sort(key=lambda parent: int(parent["line_index"]))
        ancestors = [str(parent["name"]) for parent in enclosing]
        own_name = str(item["name"])
        schema_name = str(item["schema_name"]) if item.get("schema_name") else None
        qualified_parts = [stem, *([schema_name] if schema_name else []), *ancestors, own_name]
        qualified = ".".join(qualified_parts)
        terms = [
            part for part in (*ancestors, own_name)
            if part.lower() not in _GENERIC_OPENAPI_PROPERTIES
        ]
        ancestor_blocks = [
            "\n".join(lines[int(parent["line_index"]):int(parent["end_index"]) + 1])
            for parent in enclosing
        ]
        block = "\n".join(lines[line_index:end_index + 1])
        terms.extend(
            match.group(1) or match.group(2)
            for match in _YAML_MAPPED_TO.finditer("\n".join([*ancestor_blocks, block]))
        )
        unique_terms = _unique_bounded_terms(terms)
        if not unique_terms:
            continue
        elements.append(_ElementSpan(
            PrChangedElementCandidate(
                path=path,
                name=qualified,
                line=line_index + 1,
                kind="openapi_property",
                inspection_terms=unique_terms,
            ),
            line_index + 1,
            end_index + 1,
            len(qualified_parts) - 1,
        ))
    return tuple(sorted(elements, key=lambda item: (item.start_line, item.candidate.name)))


def _openapi_schema_name(lines: Sequence[str], properties_index: int) -> str | None:
    """Return the nearest OpenAPI schema mapping key containing properties."""

    headers: list[tuple[int, int]] = []
    for index, line in enumerate(lines[:properties_index]):
        match = re.match(r"^( *)schemas[ \t]*:[ \t]*(?:#.*)?$", line)
        if match:
            headers.append((index, len(match.group(1))))
    if not headers:
        return None
    header_index, schema_indent = headers[-1]
    schema_key_indent = schema_indent + 2
    schema_name: str | None = None
    for line in lines[header_index + 1:properties_index]:
        match = _YAML_PROPERTY_KEY.match(line)
        if match and len(match.group(1)) == schema_key_indent:
            schema_name = match.group(3)
    return schema_name


def _yaml_property_block_supported(
    lines: Sequence[str],
    start: int,
    end: int,
    scalar_lines: set[int],
) -> bool:
    indentation_levels: list[int] = []
    for index in range(start, end + 1):
        line = lines[index]
        if index in scalar_lines or not line.strip() or line.lstrip().startswith("#"):
            continue
        mapping = _YAML_MAPPING_LINE.match(line)
        sequence = _YAML_SEQUENCE_LINE.match(line)
        if mapping is None and sequence is None:
            return False
        indentation = len(line) - len(line.lstrip(" "))
        if not indentation_levels:
            indentation_levels.append(indentation)
        elif indentation > indentation_levels[-1]:
            indentation_levels.append(indentation)
        else:
            while indentation_levels and indentation < indentation_levels[-1]:
                indentation_levels.pop()
            if not indentation_levels or indentation != indentation_levels[-1]:
                return False
    return True


def _yaml_block_scalar_lines(lines: Sequence[str]) -> set[int]:
    """Mark block-scalar body lines so their text is not parsed as YAML keys."""

    scalar_lines: set[int] = set()
    scalar_indent: int | None = None
    for index, line in enumerate(lines):
        if scalar_indent is not None:
            indent = len(line) - len(line.lstrip(" "))
            if line.strip() and indent > scalar_indent:
                scalar_lines.add(index)
                continue
            scalar_indent = None
        match = re.match(r"^( *)[^#]+:[ \t]*[|>][+-]?(?:[1-9])?(?:[ \t]+#.*)?$", line)
        if match:
            scalar_indent = len(line) - len(line.lstrip(" "))
    return scalar_lines


def _unique_bounded_terms(values: Sequence[str]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = value.strip()
        if not term or len(term) > 160 or term in seen:
            continue
        unique.append(term)
        seen.add(term)
        if len(unique) == 16:
            break
    return tuple(unique)


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _in_scope_changes(
    changes: list[_GitChange], scope: tuple[str, ...]
) -> tuple[list[_GitChange], list[PrContextGap]]:
    prefixes = tuple(f"{item.rstrip('/')}/" for item in scope)
    selected: list[_GitChange] = []
    outside = 0
    unsupported = 0
    for change in changes:
        if change.change not in {"A", "M", "D", "R", "C"}:
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
    changed_elements_by_path: Mapping[str, Sequence[_ElementSpan]] | None = None,
) -> tuple[list[PrChangedFile], list[PrContextGap], dict[str, int | str]]:
    try:
        root = ET.fromstring(output)
    except ET.ParseError as exc:
        raise ValueError(f"Ripwire PR-context XML parse failed: {exc}") from exc
    if root.tag != "pr-context" or root.attrib.get("schema") != "ripwire.pr-context/v1":
        raise ValueError("Ripwire output is not a ripwire.pr-context/v1 XML document")

    symbols_by_path: dict[str, tuple[PrSymbolCandidate, ...]] = {}
    impact_by_path: dict[str, tuple[PrImpactedFileCandidate, ...]] = {}
    tests_by_path: dict[str, tuple[PrAffectedTestCandidate, ...]] = {}
    file_evidence_gaps: list[PrContextGap] = []
    caller_evidence: dict[tuple[str, str, int | None, str | None], _CallerEvidence] = {}
    xml_paths: set[str] = set()
    for file_node in root.findall("./file"):
        raw_path = file_node.attrib.get("p")
        if not raw_path:
            continue
        path = _normalize_scope_path(repo_root, scope, raw_path)
        if path is None:
            continue
        xml_paths.add(path)
        impact_by_path[path], impact_gaps = _parse_impacted_files(
            repo_root, scope, file_node, path
        )
        tests_by_path[path], test_gaps = _parse_affected_tests(
            repo_root, scope, file_node, path
        )
        file_evidence_gaps.extend((*impact_gaps, *test_gaps))
        symbols: list[PrSymbolCandidate] = []
        for symbol in file_node.findall("./changed-symbols/s"):
            name = symbol.attrib.get("n")
            if not name:
                continue
            callers, evidence = _parse_callers(repo_root, scope, symbol)
            candidate = PrSymbolCandidate(
                path=path,
                name=name,
                line=_symbol_line(symbol.attrib.get("p")),
                kind=symbol.attrib.get("t"),
                callers=callers,
            )
            caller_evidence[_symbol_key(candidate)] = evidence
            symbols.append(
                candidate
            )
        symbols_by_path[path] = tuple(sorted(symbols, key=lambda item: (item.line or 0, item.name)))

    changed_files: list[PrChangedFile] = []
    gaps = [*_root_gaps(root), *file_evidence_gaps]
    missing = 0
    symbols_before = 0
    symbols_after = 0
    hunks_total = 0
    hunks_unresolved = 0
    direct_callers_before = 0
    direct_callers_after = 0
    direct_callers_unresolved = 0
    direct_callers_capped = 0
    hunks_ambiguous = 0
    element_candidates_before = sum(len(items) for items in (changed_elements_by_path or {}).values())
    element_candidates_after = 0
    hunks_resolved_by_elements = 0
    for change in changes:
        symbols = symbols_by_path.get(change.path, ())
        changed_elements: tuple[PrChangedElementCandidate, ...] = ()
        symbols_before += len(symbols)
        direct_callers_before += sum(len(symbol.callers) for symbol in symbols)
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
                selected_elements: dict[tuple[str, int], PrChangedElementCandidate] = {}
                final_unresolved = 0
                file_element_spans = (changed_elements_by_path or {}).get(change.path, ())
                for hunk_start, hunk_end in ranges:
                    hunk_symbols, _, _ = _select_hunk_symbols(
                        symbols_by_path.get(change.path, ()), ((hunk_start, hunk_end),)
                    )
                    if hunk_symbols:
                        continue
                    matching_elements = [
                        element for element in file_element_spans
                        if element.start_line <= hunk_end and hunk_start <= element.end_line
                    ]
                    if matching_elements:
                        deepest = max(element.depth for element in matching_elements)
                        best = [element for element in matching_elements if element.depth == deepest]
                        if len(best) == 1:
                            element = best[0]
                            selected_elements[(element.candidate.name, element.candidate.line)] = element.candidate
                            hunks_resolved_by_elements += 1
                            continue
                    final_unresolved += 1
                changed_elements = tuple(sorted(
                    selected_elements.values(),
                    key=lambda item: (item.line, item.name, item.kind),
                ))
                element_candidates_after += len(changed_elements)
                ambiguous = _ambiguous_hunk_groups(symbols, ranges)
                hunks_ambiguous += ambiguous
                if ambiguous:
                    gaps.append(PrContextGap(
                        kind="hunk_symbol_ambiguous",
                        detail=f"Multiple candidate symbols share changed hunk lines in {change.path}",
                        count=ambiguous,
                    ))
                hunks_unresolved += final_unresolved
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
                if final_unresolved:
                    gaps.append(
                        PrContextGap(
                            kind="hunk_symbol_unresolved",
                            detail=f"Changed hunks could not be attributed to a symbol or metadata element in {change.path}",
                            count=final_unresolved,
                        )
                    )
        for symbol in symbols:
            direct_callers_after += len(symbol.callers)
            evidence = caller_evidence.get(_symbol_key(symbol))
            if evidence is None:
                continue
            if evidence.unresolved:
                direct_callers_unresolved += evidence.unresolved
                gaps.append(
                    PrContextGap(
                        kind="caller_location_unavailable",
                        detail=f"Direct callers of {symbol.name} lacked a usable path and line",
                        count=evidence.unresolved,
                    )
                )
            if evidence.out_of_scope:
                direct_callers_unresolved += evidence.out_of_scope
                gaps.append(
                    PrContextGap(
                        kind="caller_out_of_scope",
                        detail=f"Direct callers of {symbol.name} were outside configured scope",
                        count=evidence.out_of_scope,
                    )
                )
            omitted = _caller_omitted_count(evidence, len(symbol.callers))
            if omitted:
                direct_callers_capped += omitted
                gaps.append(
                    PrContextGap(
                        kind="caller_truncated",
                        detail=f"Ripwire did not expose all direct callers of {symbol.name}",
                        count=omitted,
                    )
                )
        symbols_after += len(symbols)
        changed_files.append(
            PrChangedFile(
                path=change.path,
                change=change.change,
                old_path=change.old_path,
                symbols=symbols,
                changed_elements=changed_elements,
                impact_files=impact_by_path.get(change.path, ()),
                affected_tests=tests_by_path.get(change.path, ()),
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
                "hunks_ambiguous": hunks_ambiguous,
                "element_candidates_before": element_candidates_before,
                "element_candidates_after": element_candidates_after,
                "hunks_resolved_by_elements": hunks_resolved_by_elements,
                "relationship_selection": "direct-callers-v1",
                "direct_callers_before": direct_callers_before,
                "direct_callers_after": direct_callers_after,
                "direct_callers_unresolved": direct_callers_unresolved,
                "direct_callers_capped": direct_callers_capped,
            }
        )
    return changed_files, gaps, metrics


def _parse_impacted_files(
    repo_root: Path,
    scope: str,
    file_node: ET.Element,
    changed_path: str,
) -> tuple[tuple[PrImpactedFileCandidate, ...], list[PrContextGap]]:
    impact = file_node.find("./impact")
    if impact is None:
        return (), []
    candidates: dict[str, PrImpactedFileCandidate] = {}
    invalid = 0
    for row in impact.findall("./f"):
        path = _normalized_evidence_path(repo_root, scope, row.attrib.get("p"))
        dependent_symbols = _as_int(row.attrib.get("deps"))
        if path is None or dependent_symbols is None:
            invalid += 1
            continue
        existing = candidates.get(path)
        if existing is None or dependent_symbols > existing.dependent_symbols:
            candidates[path] = PrImpactedFileCandidate(path, dependent_symbols)
    gaps: list[PrContextGap] = []
    if invalid:
        gaps.append(PrContextGap(
            "impact_file_unavailable",
            f"Ripwire impact-file rows for {changed_path} were malformed or outside configured scope",
            invalid,
        ))
    omitted = _omitted_rows(impact, len(candidates))
    if omitted:
        gaps.append(PrContextGap(
            "impact_truncated",
            f"Ripwire did not expose all impacted files for {changed_path}",
            omitted,
        ))
    return tuple(candidates[path] for path in sorted(candidates)), gaps


def _parse_affected_tests(
    repo_root: Path,
    scope: str,
    file_node: ET.Element,
    changed_path: str,
) -> tuple[tuple[PrAffectedTestCandidate, ...], list[PrContextGap]]:
    tests = file_node.find("./tests")
    if tests is None:
        return (), []
    candidates: dict[str, PrAffectedTestCandidate] = {}
    invalid = 0
    for row in tests.findall("./test"):
        path = _normalized_evidence_path(repo_root, scope, row.attrib.get("p"))
        if path is None:
            invalid += 1
            continue
        runner = row.attrib.get("run") or None
        candidates[path] = PrAffectedTestCandidate(path, runner)
    gaps: list[PrContextGap] = []
    if invalid:
        gaps.append(PrContextGap(
            "affected_test_unavailable",
            f"Ripwire affected-test rows for {changed_path} were malformed or outside configured scope",
            invalid,
        ))
    omitted = _omitted_rows(tests, len(candidates), total_attribute="count")
    if omitted:
        gaps.append(PrContextGap(
            "affected_tests_truncated",
            f"Ripwire did not expose all affected tests for {changed_path}",
            omitted,
        ))
    return tuple(candidates[path] for path in sorted(candidates)), gaps


def _normalized_evidence_path(repo_root: Path, scope: str, value: str | None) -> str | None:
    if not value:
        return None
    raw = Path(value)
    if not raw.is_absolute() and ".." in raw.parts:
        return None
    path = _normalize_scope_path(repo_root, scope, value)
    if path is None or not _path_in_scope(path, scope):
        return None
    return path


def _omitted_rows(
    node: ET.Element,
    normalized_count: int,
    *,
    total_attribute: str = "files_other",
) -> int:
    total = _as_int(node.attrib.get(total_attribute))
    shown = _as_int(node.attrib.get("shown"))
    capped = node.attrib.get("capped") in {"1", "true"}
    if total is not None:
        omitted = max(total - (shown if shown is not None else normalized_count), 0)
        return max(omitted, 1) if capped else omitted
    return 1 if capped else 0


def _parse_callers(
    repo_root: Path,
    scope: str,
    symbol: ET.Element,
) -> tuple[tuple[PrCallerCandidate, ...], _CallerEvidence]:
    callers: list[PrCallerCandidate] = []
    unresolved = 0
    out_of_scope = 0
    for caller in symbol.findall("./caller"):
        name = caller.attrib.get("n")
        raw_path, line = _split_location(caller.attrib.get("p"))
        if not name or raw_path is None or line is None or line <= 0:
            unresolved += 1
            continue
        if not Path(raw_path).is_absolute() and ".." in Path(raw_path).parts:
            out_of_scope += 1
            continue
        path = _normalize_scope_path(repo_root, scope, raw_path)
        if path is None or not _path_in_scope(path, scope):
            out_of_scope += 1
            continue
        callers.append(
            PrCallerCandidate(
                path=path,
                name=name,
                line=line,
                kind=caller.attrib.get("t"),
            )
        )
    unique = {
        (item.path, item.name, item.line, item.kind, item.confidence): item
        for item in callers
    }
    ordered = tuple(
        sorted(unique.values(), key=lambda item: (item.path, item.line, item.kind or "", item.name))
    )
    declared = _as_int(symbol.attrib.get("callers"))
    shown = _as_int(symbol.attrib.get("shown"))
    capped = symbol.attrib.get("capped") in {"1", "true"}
    return ordered, _CallerEvidence(
        unresolved=unresolved,
        out_of_scope=out_of_scope,
        declared=declared,
        shown=shown,
        capped=capped,
    )


def _split_location(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    path, separator, suffix = value.rpartition(":")
    if not separator or not path or not suffix.isdigit():
        return None, None
    return path, int(suffix)


def _path_in_scope(path: str, scope: str) -> bool:
    prefix = scope.rstrip("/")
    return path == prefix or path.startswith(f"{prefix}/")


def _symbol_key(symbol: PrSymbolCandidate) -> tuple[str, str, int | None, str | None]:
    return symbol.path, symbol.name, symbol.line, symbol.kind


def _caller_omitted_count(evidence: _CallerEvidence, valid_count: int) -> int:
    if evidence.declared is not None and evidence.shown is not None:
        omitted = max(evidence.declared - evidence.shown, 0)
        return max(omitted, 1) if evidence.capped else omitted
    if evidence.capped:
        return max((evidence.declared or 0) - valid_count, 1)
    return 0


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


def _ambiguous_hunk_groups(
    symbols: Sequence[PrSymbolCandidate], ranges: Sequence[tuple[int, int]]
) -> int:
    lines = {symbol.line for symbol in symbols if symbol.line is not None}
    return sum(
        1 for line in lines
        if sum(1 for symbol in symbols if symbol.line == line) > 1
        and any(start <= line <= end for start, end in ranges)
    )


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
