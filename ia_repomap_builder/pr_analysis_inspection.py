"""Bounded, read-only repository inspection for the PR coordinator."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_CALLS = 2
MAX_TERMS = 8
MAX_MATCHES_PER_TERM = 20
MAX_FILES = 10
MAX_LINES_PER_FILE = 200
MAX_FILE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 30
MAX_DISCOVERED_LITERAL_LENGTH = 256
_DISCOVERED_LITERAL = re.compile(r"[A-Za-z0-9_.$:/-]{1,256}")


@dataclass(frozen=True)
class InspectionMatch:
    path: str
    line: int
    term: str
    match_type: str = "literal"
    excerpt: str = ""


def _relative_path(root: Path, value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError("inspection paths must be repository-relative")
    resolved = (root / path).resolve(strict=True)
    root_resolved = root.resolve(strict=True)
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("inspection path escapes repository root")
    return path


def _tracked_paths(root: Path, requested: Iterable[str] | None) -> set[str]:
    args = ["git", "-C", str(root), "ls-files", "-z"]
    requested_paths = []
    if requested is not None:
        requested_paths = [_relative_path(root, item).as_posix() for item in requested]
        args += ["--", *requested_paths]
    try:
        completed = subprocess.run(
            args, check=True, capture_output=True, timeout=TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"tracked-file inspection failed: {exc}") from exc
    tracked = {item.decode("utf-8") for item in completed.stdout.split(b"\0") if item}
    if requested_paths and tracked != set(requested_paths):
        missing = sorted(set(requested_paths) - tracked)
        raise ValueError(f"inspection paths are not tracked: {', '.join(missing)}")
    return tracked


def _matching_paths(root: Path, terms: list[str], requested: Iterable[str] | None) -> set[str]:
    """Find matching tracked paths before applying the result-file cap."""
    if requested is not None:
        candidates = _tracked_paths(root, requested)
        if not candidates:
            return set()
        scope = sorted(candidates)
    else:
        scope = []
    # Keep binary matches in the path list so the bounded reader can disclose
    # them as an explicit gap instead of silently dropping them at Git level.
    args = ["git", "-C", str(root), "grep", "-l", "-z", "-F"]
    for term in sorted(terms):
        args.extend(["-e", term])
    args.extend(["HEAD", "--", *scope])
    try:
        completed = subprocess.run(
            args, check=False, capture_output=True, timeout=TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"tracked-file inspection failed: {exc}") from exc
    if completed.returncode not in (0, 1):
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"tracked-file inspection failed: {detail or completed.returncode}")
    paths = set()
    for item in completed.stdout.split(b"\0"):
        if not item:
            continue
        value = item.decode("utf-8")
        # ``git grep`` prefixes paths with the searched tree-ish when an
        # explicit revision is supplied (``HEAD:path``).
        if value.startswith("HEAD:"):
            value = value[5:]
        paths.add(value)
    return paths


def inspect_repository(
    root: Path,
    terms: Iterable[str],
    paths: Iterable[str] | None = None,
    *,
    authorized_terms: Iterable[str] = (),
    authorized_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Search tracked text files for authorized literal terms.

    This function has no shell string interface.  All Git arguments and file
    reads are host-created and bounded.
    """

    root = root.resolve(strict=True)
    term_list = list(terms)
    if not 1 <= len(term_list) <= MAX_TERMS or any(not isinstance(t, str) or not t for t in term_list):
        raise ValueError(f"inspection requires one to {MAX_TERMS} non-empty literal terms")
    if len(set(term_list)) != len(term_list):
        raise ValueError("inspection terms must be unique")
    allowed = set(authorized_terms)
    unauthorized = sorted(set(term_list) - allowed)
    if unauthorized:
        raise ValueError(f"inspection terms are not authorized: {', '.join(unauthorized)}")
    requested_paths = list(paths) if paths is not None else None
    if requested_paths is not None and len(requested_paths) > MAX_FILES:
        raise ValueError(f"inspection accepts at most {MAX_FILES} paths")
    allowed_path_set = set(authorized_paths)
    if requested_paths is not None:
        unauthorized_paths = sorted(set(requested_paths) - allowed_path_set)
        if unauthorized_paths:
            raise ValueError(f"inspection paths are not authorized: {', '.join(unauthorized_paths)}")
    tracked = _matching_paths(root, term_list, requested_paths)
    ordered = sorted(tracked)
    selected = ordered[:MAX_FILES]
    gaps: list[dict[str, Any]] = []
    omitted_files = len(ordered) - len(selected)
    if omitted_files:
        gaps.append({
            "kind": "inspection_truncated",
            "detail": f"inspection returned the first {MAX_FILES} matching files",
            "count": omitted_files,
        })
    matches: list[InspectionMatch] = []
    term_counts = {term: 0 for term in term_list}
    capped_terms: set[str] = set()
    for relative in selected:
        path = _relative_path(root, relative)
        absolute = root / path
        try:
            resolved = absolute.resolve(strict=True)
            if absolute.is_symlink() and (resolved != root and root not in resolved.parents):
                raise ValueError("symlink escapes repository root")
            if absolute.stat().st_size > MAX_FILE_BYTES:
                gaps.append({"kind": "inspection_file_skipped", "detail": f"file exceeds {MAX_FILE_BYTES} bytes", "path": relative})
                continue
            content = absolute.read_bytes()
            if b"\0" in content:
                gaps.append({"kind": "inspection_binary_skipped", "detail": "binary file", "path": relative})
                continue
            text = content.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            gaps.append({"kind": "inspection_file_skipped", "detail": str(exc), "path": relative})
            continue
        lines = text.splitlines()
        file_matches = 0
        for term in sorted(term_list):
            for index, line in enumerate(lines, start=1):
                if term not in line:
                    continue
                if term_counts[term] >= MAX_MATCHES_PER_TERM:
                    if term not in capped_terms:
                        gaps.append({"kind": "inspection_truncated", "detail": f"match cap reached for {term}", "count": MAX_MATCHES_PER_TERM})
                        capped_terms.add(term)
                    break
                if file_matches >= MAX_LINES_PER_FILE:
                    gaps.append({"kind": "inspection_truncated", "detail": f"line cap reached for {relative}", "count": MAX_LINES_PER_FILE})
                    break
                lo = max(0, index - 2)
                hi = min(len(lines), index + 1)
                excerpt = "\n".join(lines[lo:hi])
                matches.append(InspectionMatch(relative, index, term, excerpt=excerpt))
                term_counts[term] += 1
                file_matches += 1
    matches.sort(key=lambda item: (item.path, item.line, item.term))
    if not matches:
        gaps.append({
            "kind": "inspection_no_match",
            "detail": "No authorized term matched a readable tracked file",
        })
    return {
        "status": "ok",
        "matches": [item.__dict__ for item in matches],
        "gaps": gaps,
        "metrics": {
            "terms": len(term_list),
            "files": len(selected),
            "files_total": len(ordered),
            "files_omitted": omitted_files,
            "matches": len(matches),
        },
    }


def _discovered_literals(matches: Iterable[dict[str, Any]]) -> set[str]:
    """Return bounded literals visible in in-memory inspection excerpts."""

    discovered: set[str] = set()
    for match in matches:
        excerpt = match.get("excerpt")
        if not isinstance(excerpt, str):
            continue
        for line in excerpt.splitlines():
            candidate = line.strip()
            if candidate and len(candidate) <= MAX_DISCOVERED_LITERAL_LENGTH:
                discovered.add(candidate)
            discovered.update(_DISCOVERED_LITERAL.findall(line))
    return discovered


def make_repository_inspection_tool(
    session: Any,
    *,
    repo_root: Path,
    evidence_payloads: list[tuple[str, bytes]] | None = None,
) -> Any:
    """Create the optional Strands wrapper around bounded inspection."""

    try:
        from strands import tool
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("strands-agents is required for repository inspection") from exc

    @tool
    def inspect_repository_evidence(terms: list[str], paths: list[str] | None = None) -> dict[str, Any]:
        try:
            session.consume_inspection_call()
            result = inspect_repository(
                repo_root,
                terms,
                paths,
                authorized_terms=session.authorized_inspection_terms,
                authorized_paths=session.authorized_inspection_paths,
            )
        except Exception as exc:
            session.record_tool_failure("inspection_unavailable", str(exc))
            return {
                "status": "error",
                "matches": [],
                "gaps": [{"kind": "inspection_unavailable", "detail": str(exc)}],
                "diagnostics": [str(exc)],
                "metrics": {"inspection_calls": session.inspection_calls},
                "evidence_id": None,
            }
        session.record_tool_result(result)
        session.authorize_inspection_matches(result["matches"])
        session.authorize_inspection_terms(_discovered_literals(result["matches"]))
        import json
        evidence_id = f"inspection-{session.inspection_calls:03d}"
        content = (json.dumps({"terms": sorted(terms), "paths": sorted(paths or []), "matches": [
            {key: value for key, value in match.items() if key != "excerpt"}
            for match in result["matches"]
        ], "gaps": result["gaps"]}, sort_keys=True, separators=(",", ":")) + "\n").encode()
        session.register_inspection_evidence(evidence_id, content)
        if evidence_payloads is not None:
            evidence_payloads.append((evidence_id, content))
        model_matches = [
            {
                "path": match["path"],
                "line": match["line"],
                "term": match["term"],
                "match_type": match["match_type"],
                "excerpt": match.get("excerpt", "")[:300],
            }
            for match in result["matches"][:10]
        ]
        model_result = {
            "status": result["status"],
            "matches": model_matches,
            "gaps": [
                {"kind": gap["kind"], **({"count": gap["count"]} if "count" in gap else {})}
                for gap in result["gaps"]
            ],
            "metrics": {
                key: result["metrics"][key]
                for key in ("terms", "files", "matches", "files_omitted")
                if key in result["metrics"]
            },
            "evidence_id": evidence_id,
        }
        if len(result["matches"]) > len(model_matches):
            model_result["gaps"].append({
                "kind": "inspection_model_truncated",
                "count": len(result["matches"]) - len(model_matches),
            })
        return model_result

    return inspect_repository_evidence


__all__ = ["inspect_repository", "make_repository_inspection_tool"]
