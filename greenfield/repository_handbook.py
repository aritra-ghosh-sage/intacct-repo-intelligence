"""Revision-bound L1/L2/L3 repository behavior handbook."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from greenfield.artifact_io import artifact_sha256

SCHEMA_VERSION = "0.1"
ARTIFACT_KIND = "greenfield_repository_behavior_handbook"
SHA = re.compile(r"^[0-9a-f]{40}$")


class RepositoryHandbookError(ValueError):
    """Raised when handbook locators cannot be verified against source."""


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RepositoryHandbookError(
            "handbook source read failed: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout


def _source_locator(
    root: Path, revision: str, path: str, line: int | None
) -> dict[str, Any]:
    content = _git(root, "show", f"{revision}:{path}")
    lines = content.decode("utf-8", errors="replace").splitlines()
    if line is not None and (line < 1 or line > len(lines)):
        raise RepositoryHandbookError(
            f"handbook locator is outside source: {path}:{line}"
        )
    start = max(1, (line or 1) - 3)
    end = min(len(lines), (line or min(20, len(lines))) + 3)
    return {
        "path": path,
        "line": line,
        "source_revision": revision,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "excerpt": "\n".join(lines[start - 1 : end]),
        "excerpt_start_line": start,
        "excerpt_end_line": end,
    }


def build_repository_handbook(
    contract: Mapping[str, Any], source_root: str | Path
) -> dict[str, Any]:
    """Build a compact source-backed handbook from a generated behavior contract."""

    if contract.get("artifact_kind") != "generated_behavior_contract":
        raise RepositoryHandbookError("generated behavior contract is required")
    revision = str(contract.get("revision") or "")
    if not SHA.fullmatch(revision):
        raise RepositoryHandbookError("contract revision must be a lowercase SHA")
    repository = str(contract.get("repository") or "").strip()
    if not repository:
        raise RepositoryHandbookError("contract repository is required")
    root = Path(source_root).resolve()
    _git(root, "cat-file", "-e", f"{revision}^{{commit}}")
    relations = [
        row
        for row in contract.get("relations", [])
        if isinstance(row, Mapping)
        and row.get("relationship_type") == "behavior_contract"
        and row.get("status") == "active"
    ]
    edges = contract.get("generation", {}).get("edges", [])
    edge_rows = [row for row in edges if isinstance(row, Mapping)]
    l3: list[dict[str, Any]] = []
    for relation in sorted(relations, key=lambda row: str(row.get("interface_id"))):
        behavior_id = str(relation.get("interface_id"))
        paths = sorted({str(path) for path in relation.get("source_paths", [])})
        locators = []
        for path in paths:
            lines = sorted(
                {
                    int(edge["source_line"])
                    for edge in edge_rows
                    if edge.get("source_path") == path
                    and isinstance(edge.get("source_line"), int)
                }
            )
            if lines:
                locators.extend(
                    _source_locator(root, revision, path, line) for line in lines
                )
            else:
                locators.append(_source_locator(root, revision, path, None))
        l3.append(
            {
                "id": behavior_id,
                "title": behavior_id,
                "description": relation.get("description"),
                "entry_symbols": sorted(
                    str(value) for value in relation.get("source_symbols", [])
                ),
                "locators": locators,
            }
        )
    handbook: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "repository": repository,
        "revision": revision,
        "leaf_mode": "behavior",
        "sections": {
            "index": {
                "level": "L1",
                "description": "Behavior-oriented repository navigation index.",
                "stages": [row["id"] for row in l3],
            },
            "behaviors": {
                "level": "L2",
                "items": [
                    {
                        "id": row["id"],
                        "description": row["description"],
                        "entry_count": len(row["entry_symbols"]),
                        "locator_count": len(row["locators"]),
                    }
                    for row in l3
                ],
            },
            **{f"behavior:{row['id']}": {"level": "L3", **row} for row in l3},
        },
        "provenance": {
            "contract_sha256": artifact_sha256(contract),
            "source_verified": True,
            "read_only": True,
            "github_writes": "none",
            "catalog_mutation": "none",
        },
    }
    handbook["handbook_sha256"] = artifact_sha256(handbook)
    errors = validate_repository_handbook(handbook)
    if errors:
        raise RepositoryHandbookError(
            "invalid repository handbook: " + "; ".join(errors)
        )
    return handbook


def validate_repository_handbook(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return ["repository handbook must be an object"]
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if value.get("artifact_kind") != ARTIFACT_KIND:
        errors.append(f"artifact_kind must be {ARTIFACT_KIND}")
    if not isinstance(value.get("revision"), str) or not SHA.fullmatch(
        value["revision"]
    ):
        errors.append("revision must be a lowercase SHA")
    sections = value.get("sections")
    if not isinstance(sections, Mapping):
        errors.append("sections must be an object")
    else:
        if not isinstance(sections.get("index"), Mapping):
            errors.append("sections.index is required")
        if not isinstance(sections.get("behaviors"), Mapping):
            errors.append("sections.behaviors is required")
        for name, section in sections.items():
            if not isinstance(section, Mapping) or section.get("level") not in {
                "L1",
                "L2",
                "L3",
            }:
                errors.append(f"sections.{name} has an invalid level")
    digest = value.get("handbook_sha256")
    unsigned = dict(value)
    unsigned.pop("handbook_sha256", None)
    if not isinstance(digest, str) or artifact_sha256(unsigned) != digest:
        errors.append("handbook_sha256 does not match handbook")
    return errors


def resynchronize_repository_handbook(
    existing: Mapping[str, Any],
    contract: Mapping[str, Any],
    source_root: str | Path,
    *,
    changed_paths: list[str],
) -> dict[str, Any]:
    """Rebuild after a non-empty diff and record the invalidated path scope."""

    if not changed_paths:
        return dict(existing)
    updated = build_repository_handbook(contract, source_root)
    updated["resynchronization"] = {
        "previous_handbook_sha256": existing.get("handbook_sha256"),
        "changed_paths": sorted(set(changed_paths)),
        "mode": "affected_behavior_rebuild",
    }
    updated.pop("handbook_sha256", None)
    updated["handbook_sha256"] = artifact_sha256(updated)
    return updated


def resynchronize_repository_handbook_at_revision(
    existing: Mapping[str, Any],
    source_root: str | Path,
    *,
    revision: str,
    changed_paths: list[str],
) -> dict[str, Any]:
    """Refresh captured L3 locators against a validated temporary revision."""
    if not SHA.fullmatch(revision):
        raise RepositoryHandbookError("resynchronization revision is invalid")
    errors = validate_repository_handbook(existing)
    if errors:
        raise RepositoryHandbookError(
            "invalid captured repository handbook: " + "; ".join(errors)
        )
    updated = deepcopy(dict(existing))
    updated["revision"] = revision
    for name, section in updated.get("sections", {}).items():
        if not isinstance(section, dict) or section.get("level") != "L3":
            continue
        locators = section.get("locators", [])
        refreshed = []
        for locator in locators:
            if not isinstance(locator, Mapping) or not locator.get("path"):
                raise RepositoryHandbookError(
                    f"stale or unresolvable locator in {name}"
                )
            refreshed.append(
                _source_locator(
                    Path(source_root).resolve(),
                    revision,
                    str(locator["path"]),
                    locator.get("line"),
                )
            )
        section["locators"] = refreshed
    updated["resynchronization"] = {
        "previous_handbook_sha256": existing.get("handbook_sha256"),
        "changed_paths": sorted(set(changed_paths)),
        "mode": "validated_ephemeral_revision",
        "revision": revision,
    }
    updated.pop("handbook_sha256", None)
    updated["handbook_sha256"] = artifact_sha256(updated)
    errors = validate_repository_handbook(updated)
    if errors:
        raise RepositoryHandbookError(
            "invalid resynchronized handbook: " + "; ".join(errors)
        )
    return updated


__all__ = [
    "ARTIFACT_KIND",
    "RepositoryHandbookError",
    "build_repository_handbook",
    "resynchronize_repository_handbook",
    "resynchronize_repository_handbook_at_revision",
    "validate_repository_handbook",
]
