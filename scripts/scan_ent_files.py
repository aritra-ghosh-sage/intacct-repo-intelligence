#!/usr/bin/env python3
"""
Deterministically scan ia-app/app for .ent files and emit JSONL metadata.

For each .ent file:
  - compute canonical PascalCase entity name
    - verify companion classes on disk (never guessed)
    - parse top-level table/view/dummy from the entity definition
    - resolve matching OpenAPI schema/api/history files from the existing spec tree
  - emit one JSON object per line

Usage:
    python scripts/scan_ent_files.py \
        --repo-root /path/to/ia-app \
        --out /path/to/entity_definitions.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from tqdm import tqdm

CLASS_EXTS = (
    ".cls",
    ".php",
    ".ent",
    ".inc",
    ".cqry",
    ".qry",
    ".xslt",
    ".phtml",
    ".html",
    ".js",
)
MISSING_METADATA_LOG_REL = Path("outputs/missing_metadata.jsonl")
SCHEMA_FILE_RE = re.compile(
    r"^(?P<kind>objects|services)\.(?P<prefix>.+?)\.(?P<object>[^.]+)\.s1\.schema\.yaml$"
)
WORKFLOW_SCHEMA_FILE_RE = re.compile(
    r"^workflows\.(?P<prefix>[^.]+)\.(?P<workflow>.+)\.s1\.schema\.yaml$"
)

REQUIRE_INCLUDE_ENT_RE = re.compile(
    r"""(?im)^\s*(?:include|require)(?:_once)?\s*(?:\(\s*)?['"]([^'"]+\.ent)['"]\s*(?:\)\s*)?;?"""
)

ALLOWED_COMPANION_ROLES = (
    # CORE
    "manager",
    "editor",
    "lister",
    "picker",
    "allowed_operations_handler",
    "approval_manager",
    "reverse_manager",
    "item_manager",
    "batch_manager",
    "batch_picker",
    # OPTIONAL
    "form_editor",
    "entity_manager",
    "entry_manager",
    "pick_manager",
    "pick_picker",
)


@dataclass
class EntityDefinition:
    entity_name: str
    ent_file: Optional[str]
    module: Optional[str]
    module_source: Optional[str]
    module_path_hint: Optional[str]
    table: Optional[str]
    view: Optional[str]
    dummy: bool
    companion_classes: Dict[str, Optional[str]]
    openapi_prefix: Optional[str]
    openapi_module: Optional[str]
    openapi_folder: Optional[str]
    openapi_schema_file: Optional[str]
    openapi_api_file: Optional[str]
    openapi_history_file: Optional[str]
    x_mapped_to: Optional[str]
    openapi_status: str
    openapi_reason: Optional[str]
    workflow_prefix: Optional[str]
    workflow_module: Optional[str]
    workflow_folder: Optional[str]
    workflow_schema_file: Optional[str]
    workflow_api_files: Optional[List[str]]
    workflow_history_file: Optional[str]
    workflow_x_mapped_to: Optional[str]
    workflow_status: str
    workflow_reason: Optional[str]
    service_prefix: Optional[str]
    service_module: Optional[str]
    service_folder: Optional[str]
    service_schema_file: Optional[str]
    service_api_files: Optional[List[str]]
    service_history_file: Optional[str]
    service_x_mapped_to: Optional[str]
    service_status: str
    service_reason: Optional[str]
    xslt_files: Optional[List[str]]


def to_repo_relative(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def build_prefix_acronyms(repo_root: Path) -> Dict[str, str]:
    """
    Build acronym hints from module folders under app/source only.
    This intentionally avoids class-derived guesses.
    """
    hints: Dict[str, str] = {}
    source_dir = repo_root / "app" / "source"
    if not source_dir.exists():
        return hints

    # Derive strictly from module directories.
    for module_dir in sorted([p for p in source_dir.iterdir() if p.is_dir()]):
        key = module_dir.name.lower()
        if key:
            hints[key] = module_dir.name.upper()

    return hints


def fallback_pascal_case(stem: str, prefix_acronyms: Dict[str, str]) -> str:
    """
    Deterministic fallback if no companion class reveals canonical casing.
    """
    parts = [p for p in re.split(r"[_\-]+", stem) if p]
    out: List[str] = []

    for part in parts:
        low = part.lower()
        if low in prefix_acronyms:
            out.append(prefix_acronyms[low])
            continue

        matched_prefix = None
        for pfx, acr in sorted(prefix_acronyms.items(), key=lambda x: -len(x[0])):
            if low.startswith(pfx) and len(part) > len(pfx):
                matched_prefix = f"{acr}{part[len(pfx) :].capitalize()}"
                break

        out.append(matched_prefix if matched_prefix else part.capitalize())

    return "".join(out) if out else stem.capitalize()


def find_module_from_ent_path(ent_path: Path, repo_root: Path) -> Optional[str]:
    """
    Module is the first folder under app/source/.
    """
    rel_parts = ent_path.relative_to(repo_root).parts
    try:
        source_idx = rel_parts.index("source")
    except ValueError:
        return None

    module_idx = source_idx + 1
    if module_idx < len(rel_parts):
        return rel_parts[module_idx]
    return None

def discover_xslt_files(ent_stem: str, entity_dir: Path, repo_root: Path) -> List[str]:
    """
    Discover XSLT files in the entity directory that match the entity stem.
    """
    xslt_files: List[str] = []
    stem_low = ent_stem.lower()

    for xslt_file in sorted(entity_dir.glob("*.xsl")):
        xslt_stem = xslt_file.stem
        if xslt_stem.lower().startswith(stem_low):
            xslt_files.append(to_repo_relative(xslt_file, repo_root))

    return xslt_files

def iter_class_files(entity_dir: Path) -> Iterable[Path]:
    for p in sorted(entity_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in CLASS_EXTS:
            yield p


def pascal_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def slug_to_pascal(value: str) -> str:
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", value) if part]
    return "".join(part.capitalize() for part in parts)


def derive_openapi_prefix(entity_name: str) -> str:
    """
    Legacy fallback prefix derivation used when x-mappedTo matching fails.
    """
    normalized = pascal_to_snake(entity_name).replace("_", "-")
    return re.sub(r"-{2,}", "-", normalized).strip("-")


def discover_companions(
    ent_stem: str,
    entity_dir: Path,
    repo_root: Path,
    prefix_acronyms: Dict[str, str],
) -> Tuple[str, Dict[str, Optional[str]]]:
    """
    Verify companion classes from disk only.

    Companion matching rule (source-trail only, no guessed suffix list):
      class_stem.lower().startswith(ent_stem.lower()) and has non-empty suffix

    Role key is derived from discovered class suffix (PascalCase -> snake_case).
    Canonical entity_name is derived from discovered class prefix; fallback is
    deterministic and uses code-derived acronym hints.
    """
    companions_raw: Dict[str, List[Path]] = {}
    canonical_candidates: List[str] = []
    stem_low = ent_stem.lower()
    stem_len = len(ent_stem)

    for cls_file in iter_class_files(entity_dir):
        cls_stem = cls_file.stem
        if not cls_stem.lower().startswith(stem_low):
            continue

        suffix = cls_stem[stem_len:]
        if not suffix:
            continue

        role = pascal_to_snake(suffix)
        if role not in ALLOWED_COMPANION_ROLES:
            continue

        companions_raw.setdefault(role, []).append(cls_file)

        canonical_candidates.append(cls_stem[:stem_len])

    companions: Dict[str, Optional[str]] = {
        role: None for role in ALLOWED_COMPANION_ROLES
    }
    for role, paths in sorted(companions_raw.items()):
        chosen = sorted(paths, key=lambda p: p.as_posix())[0]
        companions[role] = to_repo_relative(chosen, repo_root)

    if canonical_candidates:
        counts = Counter(canonical_candidates)
        # Deterministic tie-break: highest count, then lexicographically.
        canonical = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]
    else:
        canonical = fallback_pascal_case(ent_stem, prefix_acronyms)

    return canonical, companions


@dataclass
class OpenApiMapping:
    x_mapped_to: str
    openapi_prefix: str
    openapi_module: str
    openapi_folder: str
    openapi_schema_file: str
    openapi_api_file: str
    openapi_history_file: str
    openapi_status: str
    openapi_reason: Optional[str]


@dataclass
class WorkflowMapping:
    workflow_x_mapped_to: str
    workflow_prefix: str
    workflow_module: str
    workflow_folder: str
    workflow_schema_file: str
    workflow_api_files: List[str]
    workflow_history_file: Optional[str]
    workflow_status: str
    workflow_reason: Optional[str]


@dataclass
class ServiceMapping:
    service_x_mapped_to: Optional[str]
    service_prefix: str
    service_module: str
    service_folder: str
    service_schema_file: str
    service_api_files: List[str]
    service_history_file: Optional[str]
    service_status: str
    service_reason: Optional[str]


def _read_openapi_root_x_mapped_to(schema_path: Path) -> Optional[str]:
    try:
        text = schema_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    match = re.search(r"(?m)^\s*x-mappedTo:\s*([^\s#]+)", text)
    if not match:
        return None

    return match.group(1).strip().strip("\"'")


def _iter_openapi_schema_infos(
    repo_root: Path,
) -> Iterable[Tuple[Path, re.Match[str], Optional[str]]]:
    openapi_root = repo_root / "app" / "source" / "openapispec"
    if not openapi_root.exists():
        return []

    items: List[Tuple[Path, re.Match[str], Optional[str]]] = []
    for schema_path in sorted(
        openapi_root.rglob("*.schema.yaml"), key=lambda p: p.as_posix()
    ):
        schema_match = SCHEMA_FILE_RE.match(schema_path.name)
        if not schema_match:
            continue
        x_mapped_to = _read_openapi_root_x_mapped_to(schema_path)
        items.append((schema_path, schema_match, x_mapped_to))
    return items


def _build_openapi_mapping(
    repo_root: Path,
    schema_path: Path,
    schema_match: re.Match[str],
    x_mapped_to: str,
) -> OpenApiMapping:
    kind = schema_match.group("kind")
    prefix = schema_match.group("prefix")
    object_name = schema_match.group("object")
    openapi_module = schema_path.parent.parent.name
    openapi_folder = to_repo_relative(schema_path.parent.parent, repo_root)

    object_name_candidates: List[str] = [object_name]
    if kind == "services":
        for suffix in ("-request", "-response"):
            if object_name.endswith(suffix):
                base_object_name = object_name[: -len(suffix)]
                if base_object_name:
                    object_name_candidates.append(base_object_name)
                break

    api_paths = [
        schema_path.parent.parent / "paths" / f"{kind}.{prefix}.{candidate}.s1.api.yaml"
        for candidate in object_name_candidates
    ]
    history_paths = [
        schema_path.parent.parent
        / "history"
        / f"{kind}.{prefix}.{candidate}.schema.history.yaml"
        for candidate in object_name_candidates
    ]

    api_path = next((path for path in api_paths if path.exists()), api_paths[0])
    history_path = next(
        (path for path in history_paths if path.exists()), history_paths[0]
    )

    api_exists = api_path.exists()
    history_exists = history_path.exists()
    status = "ok"
    reason: Optional[str] = None
    if not api_exists or not history_exists:
        status = "partial"
        missing: List[str] = []
        if not api_exists:
            missing.append("api")
        if not history_exists:
            missing.append("history")
        reason = f"missing {' and '.join(missing)} file(s)"

    return OpenApiMapping(
        x_mapped_to=x_mapped_to,
        openapi_prefix=prefix,
        openapi_module=openapi_module,
        openapi_folder=openapi_folder,
        openapi_schema_file=to_repo_relative(schema_path, repo_root),
        openapi_api_file=to_repo_relative(api_path, repo_root),
        openapi_history_file=to_repo_relative(history_path, repo_root),
        openapi_status=status,
        openapi_reason=reason,
    )


def build_openapi_index(repo_root: Path) -> Dict[str, OpenApiMapping]:
    """
    Build an index from the OpenAPI spec tree keyed by root x-mappedTo.
    """
    index: Dict[str, OpenApiMapping] = {}
    for schema_path, schema_match, x_mapped_to in _iter_openapi_schema_infos(repo_root):
        if not x_mapped_to:
            continue

        key = x_mapped_to.lower()
        if key in index:
            # Keep the first discovered mapping for determinism.
            continue

        index[key] = _build_openapi_mapping(
            repo_root, schema_path, schema_match, x_mapped_to
        )

    return index


def build_openapi_name_index(repo_root: Path) -> Dict[str, OpenApiMapping]:
    """
    Build a name-based index keyed by schema object name for fallback matching.
    """
    index: Dict[str, OpenApiMapping] = {}
    for schema_path, schema_match, x_mapped_to in _iter_openapi_schema_infos(repo_root):
        mapping = _build_openapi_mapping(
            repo_root, schema_path, schema_match, x_mapped_to or ""
        )
        object_name = schema_match.group("object").lower()
        index.setdefault(object_name, mapping)

        if schema_match.group("kind") == "services":
            index.setdefault(f"{mapping.openapi_module}-{object_name}", mapping)
            for suffix in ("-request", "-response"):
                if object_name.endswith(suffix):
                    base_object_name = object_name[: -len(suffix)]
                    if base_object_name:
                        index.setdefault(base_object_name, mapping)
                        index.setdefault(
                            f"{mapping.openapi_module}-{base_object_name}", mapping
                        )
                    break
    return index


def _build_service_mapping(
    repo_root: Path,
    schema_path: Path,
    schema_match: re.Match[str],
    x_mapped_to: Optional[str],
) -> ServiceMapping:
    prefix = schema_match.group("prefix")
    object_name = schema_match.group("object")
    service_module = schema_path.parent.parent.name
    service_folder = to_repo_relative(schema_path.parent.parent, repo_root)

    object_name_candidates: List[str] = [object_name]
    for suffix in ("-request", "-response"):
        if object_name.endswith(suffix):
            base_object_name = object_name[: -len(suffix)]
            if base_object_name:
                object_name_candidates.append(base_object_name)
            break

    paths_dir = schema_path.parent.parent / "paths"
    apis_dir = schema_path.parent.parent / "apis"
    history_dir = schema_path.parent.parent / "history"

    api_paths: List[Path] = []
    if paths_dir.exists():
        for candidate in object_name_candidates:
            api_paths.extend(
                sorted(
                    paths_dir.glob(f"services.{prefix}.{candidate}.s1.api.yaml"),
                    key=lambda p: p.as_posix(),
                )
            )
    if apis_dir.exists():
        for candidate in object_name_candidates:
            api_paths.extend(
                sorted(
                    apis_dir.glob(f"services.{prefix}.{candidate}.s1.api.yaml"),
                    key=lambda p: p.as_posix(),
                )
            )

    deduped_api_paths: List[Path] = []
    seen_api_paths = set()
    for api_path in sorted(api_paths, key=lambda p: p.as_posix()):
        key = api_path.as_posix()
        if key in seen_api_paths:
            continue
        seen_api_paths.add(key)
        deduped_api_paths.append(api_path)

    history_candidates = [
        history_dir / f"services.{prefix}.{candidate}.schema.history.yaml"
        for candidate in object_name_candidates
    ]
    history_path = next((path for path in history_candidates if path.exists()), None)

    status = "ok"
    reason: Optional[str] = None
    has_history = history_path is not None
    if not deduped_api_paths and not has_history:
        status = "partial"
        reason = "missing api and history file(s)"
    elif not deduped_api_paths:
        status = "partial"
        reason = "missing api file(s)"
    elif not has_history:
        status = "partial"
        reason = "missing history file(s)"

    return ServiceMapping(
        service_x_mapped_to=x_mapped_to,
        service_prefix=prefix,
        service_module=service_module,
        service_folder=service_folder,
        service_schema_file=to_repo_relative(schema_path, repo_root),
        service_api_files=[
            to_repo_relative(path, repo_root) for path in deduped_api_paths
        ],
        service_history_file=to_repo_relative(history_path, repo_root)
        if history_path
        else None,
        service_status=status,
        service_reason=reason,
    )


def build_service_indexes(
    repo_root: Path,
) -> Tuple[
    Dict[str, ServiceMapping], Dict[str, ServiceMapping], Dict[str, ServiceMapping]
]:
    """
    Build service indexes:
      - by x-mappedTo (entity-centric)
      - by derived name key (fallback, including services.reports special matching)
      - by schema path (for synthetic row emission)
    """
    by_x_mapped: Dict[str, ServiceMapping] = {}
    by_name: Dict[str, ServiceMapping] = {}
    by_schema: Dict[str, ServiceMapping] = {}

    for schema_path, schema_match, x_mapped_to in _iter_openapi_schema_infos(repo_root):
        if schema_match.group("kind") != "services":
            continue

        mapping = _build_service_mapping(
            repo_root, schema_path, schema_match, x_mapped_to
        )
        by_schema.setdefault(mapping.service_schema_file, mapping)

        if x_mapped_to:
            by_x_mapped.setdefault(x_mapped_to.lower(), mapping)

        object_name = schema_match.group("object").lower()
        by_name.setdefault(object_name, mapping)
        by_name.setdefault(f"{mapping.service_module}-{object_name}", mapping)

        for suffix in ("-request", "-response"):
            if object_name.endswith(suffix):
                base_object_name = object_name[: -len(suffix)]
                if base_object_name:
                    by_name.setdefault(base_object_name, mapping)
                    by_name.setdefault(
                        f"{mapping.service_module}-{base_object_name}", mapping
                    )
                break

    return by_x_mapped, by_name, by_schema


def build_workflow_index(repo_root: Path) -> Dict[str, WorkflowMapping]:
    """
    Build an index from OpenAPI workflow schema files keyed by x-mappedTo.
    """
    index: Dict[str, WorkflowMapping] = {}
    openapi_root = repo_root / "app" / "source" / "openapispec"
    if not openapi_root.exists():
        return index

    for schema_path in sorted(
        openapi_root.rglob("workflows.*.s1.schema.yaml"), key=lambda p: p.as_posix()
    ):
        schema_match = WORKFLOW_SCHEMA_FILE_RE.match(schema_path.name)
        if not schema_match:
            continue

        x_mapped_to = _read_openapi_root_x_mapped_to(schema_path)
        if not x_mapped_to:
            continue

        prefix = schema_match.group("prefix")
        workflow_name = schema_match.group("workflow")
        workflow_module = schema_path.parent.parent.name
        workflow_folder = to_repo_relative(schema_path.parent.parent, repo_root)

        paths_dir = schema_path.parent.parent / "paths"
        apis_dir = schema_path.parent.parent / "apis"
        history_dir = schema_path.parent.parent / "history"
        history_path = (
            history_dir / f"workflows.{prefix}.{workflow_name}.schema.history.yaml"
        )

        api_paths: List[Path] = []
        if paths_dir.exists():
            api_paths.extend(
                sorted(
                    paths_dir.glob(f"workflows.{prefix}.{workflow_name}.s1.api.yaml"),
                    key=lambda p: p.as_posix(),
                )
            )
            api_paths.extend(
                sorted(
                    paths_dir.glob(f"workflows.{prefix}.{workflow_name}.*.s1.api.yaml"),
                    key=lambda p: p.as_posix(),
                )
            )
        if apis_dir.exists():
            api_paths.extend(
                sorted(
                    apis_dir.glob(f"workflows.{prefix}.{workflow_name}.s1.api.yaml"),
                    key=lambda p: p.as_posix(),
                )
            )
            api_paths.extend(
                sorted(
                    apis_dir.glob(f"workflows.{prefix}.{workflow_name}.*.s1.api.yaml"),
                    key=lambda p: p.as_posix(),
                )
            )

        # De-duplicate while preserving deterministic order.
        deduped_api_paths: List[Path] = []
        seen_api_paths = set()
        for api_path in sorted(api_paths, key=lambda p: p.as_posix()):
            key = api_path.as_posix()
            if key in seen_api_paths:
                continue
            seen_api_paths.add(key)
            deduped_api_paths.append(api_path)

        action_history_paths: List[Path] = []
        for api_path in deduped_api_paths:
            api_name = api_path.name
            if not api_name.endswith(".s1.api.yaml"):
                continue
            action_history_name = api_name.replace(
                ".s1.api.yaml", ".schema.history.yaml"
            )
            action_history_paths.append(history_dir / action_history_name)

        has_all_action_histories = bool(action_history_paths) and all(
            path.exists() for path in action_history_paths
        )

        status = "ok"
        reason: Optional[str] = None
        has_workflow_history = history_path.exists()
        has_complete_history = has_workflow_history or has_all_action_histories

        if not deduped_api_paths and not has_complete_history:
            status = "partial"
            reason = "missing api and history file(s)"
        elif not deduped_api_paths:
            status = "partial"
            reason = "missing api file(s)"
        elif not has_complete_history:
            status = "partial"
            reason = "missing history file(s)"

        key = x_mapped_to.lower()
        if key in index:
            # Keep the first discovered mapping for determinism.
            continue

        index[key] = WorkflowMapping(
            workflow_x_mapped_to=x_mapped_to,
            workflow_prefix=prefix,
            workflow_module=workflow_module,
            workflow_folder=workflow_folder,
            workflow_schema_file=to_repo_relative(schema_path, repo_root),
            workflow_api_files=[
                to_repo_relative(path, repo_root) for path in deduped_api_paths
            ],
            workflow_history_file=to_repo_relative(history_path, repo_root)
            if history_path.exists()
            else None,
            workflow_status=status,
            workflow_reason=reason,
        )

    return index


def _skip_quoted(text: str, i: int, quote: str) -> int:
    i += 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return n


def _skip_comment(text: str, i: int) -> int:
    """
    Skip PHP comments starting at index i.
    Supports:
      - // line comment
      - # line comment
      - /* block comment */
    """
    n = len(text)
    if i >= n:
        return i

    # # line comment
    if text[i] == "#":
        while i < n and text[i] != "\n":
            i += 1
        return i

    if text[i] != "/" or i + 1 >= n:
        return i

    nxt = text[i + 1]
    # // line comment
    if nxt == "/":
        i += 2
        while i < n and text[i] != "\n":
            i += 1
        return i

    # /* block comment */
    if nxt == "*":
        i += 2
        while i + 1 < n:
            if text[i] == "*" and text[i + 1] == "/":
                return i + 2
            i += 1
        return n

    return i


def _find_schema_array_start(text: str) -> Optional[int]:
    """
    Find index of opening '[' or '(' for:
      $kSchemas['name'] = [ ... ]
      $kSchemas['name'] = array( ... )
    """
    m = re.search(r"\$kSchemas\s*\[\s*['\"][^'\"]+['\"]\s*\]\s*=\s*", text)
    if not m:
        return None

    i = m.end()
    n = len(text)
    while i < n and text[i].isspace():
        i += 1

    if i < n and text[i] == "[":
        return i

    if text[i : i + 5].lower() == "array":
        j = i + 5
        while j < n and text[j].isspace():
            j += 1
        if j < n and text[j] == "(":
            return j

    return None


def parse_top_level_ent_metadata(
    ent_path: Path,
) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """
    Parse top-level 'table', 'view', 'module', and 'dummy' from .ent (PHP array syntax).
    """
    try:
        text = ent_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None, None, False

    start = _find_schema_array_start(text)
    if start is None:
        return None, None, None, False

    table: Optional[str] = None
    view: Optional[str] = None
    module: Optional[str] = None
    dummy: bool = False

    i = start + 1
    n = len(text)
    depth = 1

    while i < n and depth > 0:
        ch = text[i]

        if ch == "#" or ch == "/":
            ni = _skip_comment(text, i)
            if ni != i:
                i = ni
                continue

        if ch in ("'", '"'):
            # Try top-level key parse at depth 1.
            if depth == 1:
                key_start = i
                i = _skip_quoted(text, i, ch)
                key_text = text[key_start + 1 : i - 1]

                j = i
                while j < n and text[j].isspace():
                    j += 1

                if j + 1 < n and text[j] == "=" and text[j + 1] == ">":
                    j += 2
                    while j < n and text[j].isspace():
                        j += 1

                    # Parse scalar RHS for keys we care about.
                    rhs_value: Optional[str] = None
                    rhs_bool: Optional[bool] = None

                    if j < n and text[j] in ("'", '"'):
                        q = text[j]
                        val_start = j
                        j = _skip_quoted(text, j, q)
                        rhs_value = text[val_start + 1 : j - 1]
                    else:
                        m = re.match(r"[A-Za-z0-9_\.]+", text[j:])
                        if m:
                            token = m.group(0)
                            j += len(token)
                            low = token.lower()
                            if low in {"true", "false", "1", "0"}:
                                rhs_bool = low in {"true", "1"}
                            else:
                                rhs_value = token

                    key_low = key_text.lower()
                    if key_low == "table" and rhs_value and table is None:
                        table = rhs_value
                    elif key_low == "view" and rhs_value and view is None:
                        view = rhs_value
                    elif key_low == "module" and rhs_value and module is None:
                        module = rhs_value
                    elif key_low == "dummy":
                        if rhs_bool is not None:
                            dummy = rhs_bool
                        elif rhs_value is not None:
                            dummy = rhs_value.strip().lower() in {
                                "true",
                                "1",
                                "yes",
                                "t",
                            }

                    i = j
                    continue

            else:
                i = _skip_quoted(text, i, ch)
                continue

        if ch == "[" or ch == "(":
            depth += 1
        elif ch == "]" or ch == ")":
            depth -= 1

        i += 1

    return table, view, module, dummy


def _write_missing_metadata_log(log_path: Path, records: List[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

def extract_required_ent_paths(text: str) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []

    for match in REQUIRE_INCLUDE_ENT_RE.finditer(text):
        value = match.group(1).strip()
        if not value:
            continue
        norm = value.replace("\\", "/")
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)

    return out

def load_entity_definition_index(repo_root: Path) -> Dict[str, dict]:
    index: Dict[str, dict] = {}
    jsonl_path = repo_root / "config" / "entity_definitions.jsonl"
    if not jsonl_path.exists():
        return index

    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return index

    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue

        ent_file = row.get("ent_file")
        if isinstance(ent_file, str) and ent_file:
            rel_key = ent_file.replace("\\", "/").lstrip("./").lower()
            index.setdefault(rel_key, row)
            index.setdefault(Path(ent_file).name.lower(), row)
            index.setdefault(Path(ent_file).stem.lower(), row)

        entity_name = row.get("entity_name")
        if isinstance(entity_name, str) and entity_name:
            index.setdefault(entity_name.lower(), row)

    return index


def _resolve_required_ent_path(
    require_value: str,
    current_ent_path: Path,
    repo_root: Path,
    entity_index: Dict[str, dict],
) -> Optional[Path]:
    require_name = Path(require_value).name
    candidates: List[Path] = []

    direct_candidate = (current_ent_path.parent / require_value).resolve()
    candidates.append(direct_candidate)

    lookup_keys = [
        require_value.replace("\\", "/").lstrip("./").lower(),
        require_name.lower(),
        Path(require_name).stem.lower(),
    ]
    for key in lookup_keys:
        row = entity_index.get(key)
        if not row:
            continue
        ent_file = row.get("ent_file")
        if isinstance(ent_file, str) and ent_file:
            candidates.append((repo_root / ent_file).resolve())

    source_dir = repo_root / "app" / "source"
    if source_dir.exists():
        candidates.extend(
            sorted(source_dir.rglob(require_name), key=lambda p: p.as_posix())
        )

    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = candidate.as_posix()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate.exists():
            return candidate

    return None


def scan(repo_root: Path, out_file: Path) -> int:
    app_dir = repo_root / "app"
    if not app_dir.exists():
        raise FileNotFoundError(f"{app_dir} does not exist")

    ent_paths = sorted(app_dir.rglob("*.ent"), key=lambda p: p.as_posix())
    prefix_acronyms = build_prefix_acronyms(repo_root)
    openapi_index = build_openapi_index(repo_root)
    openapi_name_index = build_openapi_name_index(repo_root)
    workflow_index = build_workflow_index(repo_root)
    service_index, service_name_index, service_schema_index = build_service_indexes(
        repo_root
    )
    out_file.parent.mkdir(parents=True, exist_ok=True)
    missing_metadata_records: List[dict] = []

    count = 0
    consumed_service_schema_files = set()
    entity_index = load_entity_definition_index(repo_root)

    with out_file.open("w", encoding="utf-8") as f:
        for ent_path in tqdm(ent_paths, desc="Scanning .ent files", unit="file"):
            ent_stem = ent_path.stem
            canonical_name, companions = discover_companions(
                ent_stem=ent_stem,
                entity_dir=ent_path.parent,
                repo_root=repo_root,
                prefix_acronyms=prefix_acronyms,
            )
            # table, view, module_from_ent, dummy = parse_top_level_ent_metadata(ent_path)
            table, view, module_from_ent, dummy, module_source = resolve_ent_metadata(
                ent_path=ent_path,
                repo_root=repo_root,
                entity_index=entity_index,
                visited=set())

            module_path_hint = find_module_from_ent_path(ent_path, repo_root)
            module_value = module_from_ent

            if table and table.strip().lower() == "dummy":
                dummy = True

            if not module_from_ent or not table:
                missing_metadata_records.append(
                    {
                        "context": {
                            "module_path_hint": module_path_hint,
                        },
                        "entity_name": canonical_name,
                        "file_path": to_repo_relative(ent_path, repo_root),
                        "reason": "missing module key in top-level kSchemas definition" if not module_from_ent else "missing table key in top-level kSchemas definition",
                        "source": "scan_ent_files",
                        "stage": "module_extraction",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

            openapi_mapping = openapi_index.get(canonical_name.lower())
            if openapi_mapping is None:
                openapi_mapping = openapi_index.get(ent_stem.lower())

            if openapi_mapping is None:
                derived_prefix = derive_openapi_prefix(canonical_name)
                candidate_mapping = openapi_name_index.get(derived_prefix)
                if candidate_mapping is not None:
                    openapi_mapping = OpenApiMapping(
                        x_mapped_to=None,
                        openapi_prefix=derived_prefix,
                        openapi_module=candidate_mapping.openapi_module,
                        openapi_folder=candidate_mapping.openapi_folder,
                        openapi_schema_file=candidate_mapping.openapi_schema_file,
                        openapi_api_file=candidate_mapping.openapi_api_file,
                        openapi_history_file=candidate_mapping.openapi_history_file,
                        openapi_status=candidate_mapping.openapi_status,
                        openapi_reason=(
                            f"name-based fallback matched {derived_prefix}; "
                            f"x-mappedTo mismatch for {canonical_name}"
                        ),
                    )

            if openapi_mapping is None:
                openapi_mapping = OpenApiMapping(
                    x_mapped_to=None,
                    openapi_prefix=derive_openapi_prefix(canonical_name),
                    openapi_module=None,
                    openapi_folder=None,
                    openapi_schema_file=None,
                    openapi_api_file=None,
                    openapi_history_file=None,
                    openapi_status="unmapped",
                    openapi_reason=f"no OpenAPI schema with x-mappedTo matching {canonical_name}",
                )

            workflow_mapping = workflow_index.get(canonical_name.lower())
            if workflow_mapping is None:
                workflow_mapping = workflow_index.get(ent_stem.lower())

            if workflow_mapping is None:
                workflow_mapping = WorkflowMapping(
                    workflow_x_mapped_to=None,
                    workflow_prefix=None,
                    workflow_module=None,
                    workflow_folder=None,
                    workflow_schema_file=None,
                    workflow_api_files=[],
                    workflow_history_file=None,
                    workflow_status="unmapped",
                    workflow_reason=f"no workflow schema with x-mappedTo matching {canonical_name}",
                )

            service_mapping = service_index.get(canonical_name.lower())
            if service_mapping is None:
                service_mapping = service_index.get(ent_stem.lower())

            if service_mapping is None:
                derived_prefix = derive_openapi_prefix(canonical_name)
                candidate_service_mapping = service_name_index.get(derived_prefix)
                if candidate_service_mapping is not None:
                    service_mapping = ServiceMapping(
                        service_x_mapped_to=candidate_service_mapping.service_x_mapped_to,
                        service_prefix=derived_prefix,
                        service_module=candidate_service_mapping.service_module,
                        service_folder=candidate_service_mapping.service_folder,
                        service_schema_file=candidate_service_mapping.service_schema_file,
                        service_api_files=candidate_service_mapping.service_api_files,
                        service_history_file=candidate_service_mapping.service_history_file,
                        service_status=candidate_service_mapping.service_status,
                        service_reason=(
                            f"name-based fallback matched {derived_prefix}; "
                            f"x-mappedTo mismatch for {canonical_name}"
                        ),
                    )

            if service_mapping is None:
                service_mapping = ServiceMapping(
                    service_x_mapped_to=None,
                    service_prefix=None,
                    service_module=None,
                    service_folder=None,
                    service_schema_file=None,
                    service_api_files=[],
                    service_history_file=None,
                    service_status="unmapped",
                    service_reason=f"no service schema with x-mappedTo matching {canonical_name}",
                )

            if service_mapping.service_schema_file:
                consumed_service_schema_files.add(service_mapping.service_schema_file)

            discovered_xslts = discover_xslt_files(ent_stem=canonical_name, entity_dir=ent_path.parent, repo_root=repo_root)
            row = EntityDefinition(
                entity_name=canonical_name,
                ent_file=to_repo_relative(ent_path, repo_root),
                module=module_value,
                module_source=module_source,
                module_path_hint=module_path_hint,
                table=table,
                view=view,
                dummy=dummy,
                companion_classes=companions,
                openapi_prefix=openapi_mapping.openapi_prefix,
                openapi_module=openapi_mapping.openapi_module,
                openapi_folder=openapi_mapping.openapi_folder,
                openapi_schema_file=openapi_mapping.openapi_schema_file,
                openapi_api_file=openapi_mapping.openapi_api_file,
                openapi_history_file=openapi_mapping.openapi_history_file,
                x_mapped_to=openapi_mapping.x_mapped_to,
                openapi_status=openapi_mapping.openapi_status,
                openapi_reason=openapi_mapping.openapi_reason,
                workflow_prefix=workflow_mapping.workflow_prefix,
                workflow_module=workflow_mapping.workflow_module,
                workflow_folder=workflow_mapping.workflow_folder,
                workflow_schema_file=workflow_mapping.workflow_schema_file,
                workflow_api_files=workflow_mapping.workflow_api_files
                if workflow_mapping.workflow_api_files
                else None,
                workflow_history_file=workflow_mapping.workflow_history_file,
                workflow_x_mapped_to=workflow_mapping.workflow_x_mapped_to,
                workflow_status=workflow_mapping.workflow_status,
                workflow_reason=workflow_mapping.workflow_reason,
                service_prefix=service_mapping.service_prefix,
                service_module=service_mapping.service_module,
                service_folder=service_mapping.service_folder,
                service_schema_file=service_mapping.service_schema_file,
                service_api_files=service_mapping.service_api_files
                if service_mapping.service_api_files
                else None,
                service_history_file=service_mapping.service_history_file,
                service_x_mapped_to=service_mapping.service_x_mapped_to,
                service_status=service_mapping.service_status,
                service_reason=service_mapping.service_reason,
                xslt_files=discovered_xslts if discovered_xslts else None
            )
            f.write(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1

        for schema_file in tqdm(
            sorted(service_schema_index.keys()),
            desc="Emitting service-only rows",
            unit="schema",
        ):
            if schema_file in consumed_service_schema_files:
                continue

            service_mapping = service_schema_index[schema_file]
            object_token = (
                Path(schema_file).name.replace(".s1.schema.yaml", "").split(".")[-1]
            )
            synthetic_key = f"{service_mapping.service_module}.{service_mapping.service_prefix}.{object_token}"
            synthetic_entity_name = slug_to_pascal(synthetic_key)
            companion_defaults = {role: None for role in ALLOWED_COMPANION_ROLES}

            row = EntityDefinition(
                entity_name=synthetic_entity_name or object_token,
                ent_file=None,
                module=None,
                module_source=None,
                module_path_hint=None,
                table=None,
                view=None,
                dummy=False,
                companion_classes=companion_defaults,
                openapi_prefix=None,
                openapi_module=None,
                openapi_folder=None,
                openapi_schema_file=None,
                openapi_api_file=None,
                openapi_history_file=None,
                x_mapped_to=None,
                openapi_status="unmapped",
                openapi_reason="no object schema mapped to synthetic service row",
                workflow_prefix=None,
                workflow_module=None,
                workflow_folder=None,
                workflow_schema_file=None,
                workflow_api_files=None,
                workflow_history_file=None,
                workflow_x_mapped_to=None,
                workflow_status="unmapped",
                workflow_reason="no workflow schema mapped to synthetic service row",
                service_prefix=service_mapping.service_prefix,
                service_module=service_mapping.service_module,
                service_folder=service_mapping.service_folder,
                service_schema_file=service_mapping.service_schema_file,
                service_api_files=service_mapping.service_api_files
                if service_mapping.service_api_files
                else None,
                service_history_file=service_mapping.service_history_file,
                service_x_mapped_to=service_mapping.service_x_mapped_to,
                service_status=service_mapping.service_status,
                service_reason="service-only synthetic row (no .ent mapping)",
                xslt_files=None
            )
            f.write(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1

    _write_missing_metadata_log(
        (repo_root / MISSING_METADATA_LOG_REL).resolve(),
        missing_metadata_records,
    )

    return count

def resolve_ent_metadata(
    ent_path: Path | None,
    repo_root: Path | None,
    entity_index: dict[str, dict[str, str | None]],
    visited: set[str],
) -> tuple[Optional[str], Optional[str], Optional[str], bool, Optional[str]]:
    if ent_path is None or str(ent_path) in visited:
        return None, None, None, False, None

    visited.add(str(ent_path))
    try:
        try:
            text = ent_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None, None, None, False, None

        table, view, module, dummy = parse_top_level_ent_metadata(ent_path)
        module_source = "ent_metadata" if module else None

        if table is not None and module is not None:
            return table, view, module, dummy, module_source
        
        for require_value in extract_required_ent_paths(text):
            required_ent_path = _resolve_required_ent_path(
                require_value=require_value,
                current_ent_path=ent_path,
                repo_root=repo_root,
                entity_index=entity_index
            )
            if required_ent_path is None:
                continue

            parent_table, parent_view, parent_module, parent_dummy, module_source = resolve_ent_metadata(
                required_ent_path,
                repo_root,
                entity_index,
                visited,
            )

            if table is None and parent_table is not None:
                table = parent_table
            if module is None and parent_module is not None:
                module = parent_module
                module_source = f"required:{to_repo_relative(required_ent_path, repo_root)}"

            if table is not None and module is not None:
                return table, view, module, dummy, module_source

        return table, view, module, dummy, module_source
    finally:
        visited.remove(str(ent_path))

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan ia-app .ent files and emit deterministic JSONL metadata."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root (default: current directory).",
    )
    parser.add_argument(
        "--out",
        default="entity_definitions.jsonl",
        help="Output JSONL path (default: entity_definitions.jsonl).",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    out_file = Path(args.out)
    if not out_file.is_absolute():
        out_file = (repo_root / out_file).resolve()

    count = scan(repo_root, out_file)
    print(f"Wrote {count} entities to {out_file}")


if __name__ == "__main__":
    main()
