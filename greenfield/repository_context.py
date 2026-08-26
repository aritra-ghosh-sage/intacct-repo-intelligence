"""Revision-pinned repository guidance, ownership, and test-root context."""

from __future__ import annotations

import fnmatch
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_target_evidence,
)

GUIDANCE_NAMES = ("AGENTS.md", "CLAUDE.md")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _git(root: Path, revision: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "-C", str(root), "show", f"{revision}:{path}"], capture_output=True, check=False, timeout=30)
    return result.stdout if result.returncode == 0 else None


def _owner_for_path(codeowners: str | None, path: str) -> list[str]:
    """Apply CODEOWNERS last-match semantics without inventing an owner."""

    owners: list[str] = []
    if not codeowners:
        return owners
    for raw in codeowners.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        bits = line.split()
        if len(bits) < 2:
            continue
        pattern, declared = bits[0], bits[1:]
        normalized = pattern.lstrip("/")
        matches = (
            fnmatch.fnmatch(path, normalized)
            or (normalized.endswith("/") and path.startswith(normalized))
            or ("/" not in normalized and fnmatch.fnmatch(Path(path).name, normalized))
        )
        if matches:
            owners = declared
    return owners


def collect_repository_context(
    *,
    repository: str,
    revision: str,
    changed_paths: list[str],
    local_root: str | Path | None = None,
    test_roots: list[str] | None = None,
    provider=None,
) -> dict[str, Any]:
    """Collect advisory guides and CODEOWNERS at one immutable revision."""

    paths = sorted({"CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS", *GUIDANCE_NAMES})
    root = Path(local_root).resolve() if local_root else None
    files: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    source = "github"
    if root and (root / ".git").exists():
        source = "local_git"
        for path in paths:
            content = _git(root, revision, path)
            if content is not None:
                contents[path] = content
        # Scoped instructions are only advisory and are discovered at the same SHA.
        listed = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", revision, ".github/instructions"], capture_output=True, text=True, check=False, timeout=30)
        for path in listed.stdout.splitlines():
            if path.endswith(".instructions.md"):
                content = _git(root, revision, path)
                if content is not None:
                    contents[path] = content
    else:
        try:
            snapshot = collect_target_evidence(repository, revision=revision, paths=paths, provider=provider, include_content=True, allow_missing=True)
        except RepositoryEvidenceError as exc:
            return {"schema_version": "0.1", "analysis_kind": "greenfield_repository_context", "repository": repository, "inspected_revision": revision, "status": "unavailable", "gaps": [str(exc)], "guidance": [], "owners": [], "test_roots": test_roots or [], "provenance": {"read_only": True, "source": source}}
        # Target evidence intentionally retains hashes, not content; unavailable guidance is explicit.
        for row in snapshot["files"]:
            files.append({"path": row["path"], "sha256": row["content_sha256"], "kind": "guidance" if row["path"] not in {"CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"} else "codeowners", **({"content": row["content"]} if "content" in row else {})})
            if "content" in row:
                contents[row["path"]] = str(row["content"]).encode("utf-8")

    for path, content in sorted(contents.items()):
        if source == "github":
            continue
        files.append({"path": path, "sha256": _sha(content), "kind": "guidance" if path != "CODEOWNERS" and not path.endswith("CODEOWNERS") else "codeowners"})
    codeowners = next((content.decode("utf-8", errors="replace") for path, content in contents.items() if path.endswith("CODEOWNERS")), None)
    owners = [{"path": path, "owners": _owner_for_path(codeowners, path), "status": "available" if _owner_for_path(codeowners, path) else "ownership_unavailable"} for path in sorted(changed_paths)]
    return {"schema_version": "0.1", "analysis_kind": "greenfield_repository_context", "repository": repository, "inspected_revision": revision, "status": "available", "guidance": [row for row in files if row["kind"] == "guidance"], "owners": owners, "test_roots": sorted(test_roots or []), "gaps": ([] if codeowners else ["codeowners_unavailable"]), "provenance": {"read_only": True, "source": source, "files": files}}


__all__ = ["collect_repository_context"]
