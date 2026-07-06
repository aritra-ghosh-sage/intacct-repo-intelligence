#!/usr/bin/env python3
"""
Deterministically scan ia-app/app for .ent files and emit JSONL metadata.

For each .ent file:
  - compute canonical PascalCase entity name
  - verify companion classes on disk (never guessed)
  - parse top-level table/view/dummy from the entity definition
  - emit one JSON object per line

Usage:
    python scripts/scan_ent_files.py \
        scan \
        --repo-root /path/to/ia-app \
        --out /path/to/entity_definitions.jsonl
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import click
from tqdm import tqdm

CLASS_EXTS = (".cls", ".php", ".ent", ".inc", ".cqry", ".xslt", ".phtml", ".html", ".js", ".yaml")

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
    ent_file: str
    module: Optional[str]
    table: Optional[str]
    view: Optional[str]
    dummy: bool
    companion_classes: Dict[str, Optional[str]]


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
                matched_prefix = f"{acr}{part[len(pfx):].capitalize()}"
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


def role_to_suffix(role: str) -> str:
    return "".join(part.capitalize() for part in role.split("_"))


def build_class_stem_index(app_dir: Path) -> Dict[str, List[Path]]:
    """
    Index class-like files by lowercase stem for deterministic, case-insensitive lookup.
    """
    stem_index: Dict[str, List[Path]] = {}
    for path in sorted(app_dir.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in CLASS_EXTS:
            continue
        stem_index.setdefault(path.stem.lower(), []).append(path)
    return stem_index


def discover_search_roots(ent_path: Path, repo_root: Path) -> List[Path]:
    """
    Ordered directories for companion selection priority.
    """
    roots: List[Path] = []

    def _add(path: Path) -> None:
        resolved = path.resolve()
        if resolved.exists() and resolved not in roots:
            roots.append(resolved)

    app_dir = repo_root / "app"
    module = find_module_from_ent_path(ent_path, repo_root)

    _add(ent_path.parent)
    if module:
        _add(repo_root / "app" / "source" / module)

    _add(repo_root / "app" / "source" / "common")
    _add(repo_root / "app" / "source" / "core")
    _add(repo_root / "app" / "common")
    _add(repo_root / "app" / "core")
    _add(repo_root / "app" / "resources" / "js")
    _add(app_dir)
    return roots


def pick_best_candidate(candidates: List[Path], search_roots: List[Path]) -> Path:
    def root_priority(path: Path) -> int:
        resolved = path.resolve()
        for idx, root in enumerate(search_roots):
            try:
                resolved.relative_to(root)
                return idx
            except ValueError:
                continue
        return len(search_roots)

    return sorted(candidates, key=lambda p: (root_priority(p), p.as_posix()))[0]


def discover_companions(
    ent_stem: str,
    ent_path: Path,
    repo_root: Path,
    class_stem_index: Dict[str, List[Path]],
    prefix_acronyms: Dict[str, str],
) -> Tuple[str, Dict[str, Optional[str]]]:
    """
    Verify companion classes from indexed files only.
    """
    search_roots = discover_search_roots(ent_path, repo_root)
    canonical_candidates: List[str] = []

    companions: Dict[str, Optional[str]] = {role: None for role in ALLOWED_COMPANION_ROLES}
    for role in ALLOWED_COMPANION_ROLES:
        suffix = role_to_suffix(role)
        stem_key = f"{ent_stem.lower()}{suffix.lower()}"
        candidates = class_stem_index.get(stem_key, [])
        if not candidates:
            continue

        chosen = pick_best_candidate(candidates, search_roots)
        companions[role] = to_repo_relative(chosen, repo_root)
        canonical_candidates.append(chosen.stem[: len(chosen.stem) - len(suffix)])

    if canonical_candidates:
        counts = Counter(canonical_candidates)
        # Deterministic tie-break: highest count, then lexicographically.
        canonical = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]
    else:
        canonical = fallback_pascal_case(ent_stem, prefix_acronyms)

    return canonical, companions


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


def parse_top_level_ent_metadata(ent_path: Path) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Parse top-level 'table', 'view', and 'dummy' from .ent (PHP array syntax).
    """
    try:
        text = ent_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None, False

    start = _find_schema_array_start(text)
    if start is None:
        return None, None, False

    table: Optional[str] = None
    view: Optional[str] = None
    dummy: bool = False

    i = start + 1
    n = len(text)
    depth = 1

    while i < n and depth > 0:
        ch = text[i]

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
                    elif key_low == "dummy":
                        if rhs_bool is not None:
                            dummy = rhs_bool
                        elif rhs_value is not None:
                            dummy = rhs_value.strip().lower() in {"true", "1", "yes", "t"}

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

    return table, view, dummy


def scan(repo_root: Path, out_file: Path) -> int:
    app_dir = repo_root / "app"
    if not app_dir.exists():
        raise FileNotFoundError(f"{app_dir} does not exist")

    ent_paths = sorted(app_dir.rglob("*.ent"), key=lambda p: p.as_posix())
    prefix_acronyms = build_prefix_acronyms(repo_root)
    class_stem_index = build_class_stem_index(app_dir)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with out_file.open("w", encoding="utf-8") as f:
        for ent_path in tqdm(ent_paths, desc="Scanning .ent files", unit="file"):
            ent_stem = ent_path.stem
            canonical_name, companions = discover_companions(
                ent_stem=ent_stem,
                ent_path=ent_path,
                repo_root=repo_root,
                class_stem_index=class_stem_index,
                prefix_acronyms=prefix_acronyms,
            )
            table, view, dummy = parse_top_level_ent_metadata(ent_path)

            row = EntityDefinition(
                entity_name=canonical_name,
                ent_file=to_repo_relative(ent_path, repo_root),
                module=find_module_from_ent_path(ent_path, repo_root),
                table=table,
                view=view,
                dummy=dummy,
                companion_classes=companions,
            )
            f.write(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1

    return count


@click.group()
def cli() -> None:
    pass


@cli.command("scan")
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    default=Path("."),
    show_default=True,
    help="Repository root.",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("config/entity_definitions.jsonl"),
    show_default=True,
    help="Output JSONL path.",
)
def scan_command(repo_root: Path, out: Path) -> None:
    repo_root = repo_root.resolve()
    out_file = out
    if not out_file.is_absolute():
        out_file = (repo_root / out_file).resolve()

    count = scan(repo_root, out_file)
    click.echo(f"Wrote {count} entities to {out_file}")


if __name__ == "__main__":
    cli()
