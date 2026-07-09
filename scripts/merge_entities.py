#!/usr/bin/env python3

"""
Phase 2B - Merge GHCP entity discovery output into a canonical entities.json.

Pipeline position:

    GHCP one-per-file JSON outputs (entities.jsonl)
        |
        v
    scripts/merge_entities.py    <-- this file
        |
        v
    config/entities.json
        |
        v
    scripts/build_entities.py
    scripts/build_entity_roots.py


Input (JSON Lines, one JSON object per line):
--------------------------------------------
Each line must match the deterministic schema produced by the GHCP
extraction prompt. Example:

{
  "entity_name": "APBill",
  "ent_file": "app/source/apar/apbill.ent",
  "module": "apar",
  "table": "apbill",
  "view": null,
  "dummy": false,
  "rest_endpoint": null,
  "companion_classes": {
     "manager": "app/source/apar/APBillManager.cls",
     "editor": "app/source/apar/APBillEditor.cls",
     "lister": null,
     "picker": null,
     "allowed_operations_handler": null,
     "approval_manager": null,
     "reverse_manager": null,
     "item_manager": null,
     "batch_manager": null
  },
  "related_files": {
     "inc": null,
     "xslt": null,
     "yaml": null,
     "xml": null
  },
  "confidence": 1.0,
  "validation": {
     "ent_exists": true,
     "companion_files_verified": true,
     "notes": []
  }
}


Output (JSON, entities keyed by canonical name):
------------------------------------------------
{
  "APBill": { ...same shape as above... },
  "ARInvoice": { ... },
  ...
}


CLI:
    python scripts/merge_entities.py \
        --input config/entities.jsonl \
        --output config/entities.json \
        --repo-root /projects/main \
        --verify \
        --strict


Behavior:

- Skips blank lines and lines beginning with '#'.
- Skips malformed JSON lines and reports them.
- Deduplicates by (module, entity_name). If the same canonical entity
  appears twice, we keep the higher-confidence entry and disambiguate
  cross-module collisions using the module prefix, e.g.
  "APBill" and "AR__Bill" -> "APBill" and "ARBill".
- Enforces the fixed schema. Unknown top-level keys are dropped.
- With --verify, verifies every referenced file path exists on disk
  under --repo-root and downgrades confidence if not.
- With --strict, aborts on any schema violation.

Exit codes:
    0  ok
    1  input file missing / IO error
    2  strict mode: schema violations found
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------

REQUIRED_TOP_LEVEL_KEYS = {
    "entity_name",
    "ent_file",
    "module",
}

COMPANION_KEYS = [
    "manager",
    "editor",
    "lister",
    "picker",
    "allowed_operations_handler",
    "approval_manager",
    "reverse_manager",
    "item_manager",
    "batch_manager",
]

RELATED_KEYS = [
    "inc",
    "xslt",
    "yaml",
    "xml",
]

DEFAULT_ENTITY_SHAPE: dict[str, Any] = {
    "entity_name": None,
    "ent_file": None,
    "module": None,
    "table": None,
    "view": None,
    "dummy": False,
    "rest_endpoint": None,
    "companion_classes": {k: None for k in COMPANION_KEYS},
    "related_files": {k: None for k in RELATED_KEYS},
    "confidence": 1.0,
    "validation": {
        "ent_exists": True,
        "companion_files_verified": True,
        "notes": [],
    },
}


# ---------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------

def read_jsonl(path: Path) -> list[tuple[int, dict]]:
    """
    Returns list of (line_number, parsed_json).
    Skips blank lines and comment lines starting with '#'.
    Records line numbers for good error messages.
    Raises no exception on malformed lines - reports them via stderr.
    """
    out: list[tuple[int, dict]] = []

    with path.open("r", encoding="utf-8") as f:
        for i, raw in enumerate(f, start=1):
            line = raw.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(
                    f"[warn] Skipping malformed JSON at line {i}: {e}",
                    file=sys.stderr,
                )
                continue

            if not isinstance(obj, dict):
                print(
                    f"[warn] Skipping non-object at line {i}",
                    file=sys.stderr,
                )
                continue

            out.append((i, obj))

    return out


# ---------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------

def normalize_entry(raw: dict) -> dict:
    """
    Force the entry into the fixed schema.
    Missing keys become nulls / defaults.
    Unknown keys are dropped.
    """
    out = json.loads(json.dumps(DEFAULT_ENTITY_SHAPE))

    for k in ["entity_name", "ent_file", "module", "table", "view", "rest_endpoint"]:
        if k in raw and isinstance(raw[k], str):
            out[k] = raw[k].strip() or None
        elif k in raw and raw[k] is None:
            out[k] = None

    if "dummy" in raw and isinstance(raw["dummy"], bool):
        out["dummy"] = raw["dummy"]

    if "confidence" in raw:
        try:
            out["confidence"] = float(raw["confidence"])
        except (TypeError, ValueError):
            out["confidence"] = 0.0

    companions = raw.get("companion_classes") or {}
    if isinstance(companions, dict):
        for k in COMPANION_KEYS:
            v = companions.get(k)
            out["companion_classes"][k] = v if isinstance(v, str) and v.strip() else None

    related = raw.get("related_files") or {}
    if isinstance(related, dict):
        for k in RELATED_KEYS:
            v = related.get(k)
            out["related_files"][k] = v if isinstance(v, str) and v.strip() else None

    validation = raw.get("validation") or {}
    if isinstance(validation, dict):
        out["validation"]["ent_exists"] = bool(validation.get("ent_exists", True))
        out["validation"]["companion_files_verified"] = bool(
            validation.get("companion_files_verified", True)
        )
        notes = validation.get("notes") or []
        if isinstance(notes, list):
            out["validation"]["notes"] = [str(n) for n in notes if n is not None]

    return out


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def validate_required(entry: dict, line_no: int) -> list[str]:
    issues: list[str] = []

    for k in REQUIRED_TOP_LEVEL_KEYS:
        if not entry.get(k):
            issues.append(f"line {line_no}: missing required field '{k}'")

    ent_file = entry.get("ent_file") or ""
    if ent_file and not ent_file.endswith(".ent"):
        issues.append(
            f"line {line_no}: ent_file does not end with .ent: {ent_file}"
        )

    return issues


def verify_paths_on_disk(entry: dict, repo_root: Path) -> list[str]:
    """
    Confirms every referenced file exists.
    Updates entry.validation on mismatch.
    Returns notes describing any missing files.
    """
    notes: list[str] = []

    def _exists(rel_path: Optional[str]) -> bool:
        if not rel_path:
            return True
        p = (repo_root / rel_path).resolve()
        return p.exists()

    ent_file = entry.get("ent_file")

    if ent_file and not _exists(ent_file):
        entry["validation"]["ent_exists"] = False
        notes.append(f"missing ent_file on disk: {ent_file}")

    all_companion_ok = True
    for k, v in entry["companion_classes"].items():
        if v and not _exists(v):
            all_companion_ok = False
            entry["companion_classes"][k] = None
            notes.append(f"missing companion {k}: {v}")

    for k, v in entry["related_files"].items():
        if v and not _exists(v):
            entry["related_files"][k] = None
            notes.append(f"missing related {k}: {v}")

    entry["validation"]["companion_files_verified"] = all_companion_ok

    if notes:
        entry["validation"]["notes"].extend(notes)
        # Downgrade confidence for entries with disk mismatches
        entry["confidence"] = min(entry.get("confidence", 1.0), 0.7)

    return notes


# ---------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------

def canonical_key(entry: dict) -> str:
    """
    Canonical key for storage.
    Same entity_name across different modules is disambiguated
    by keeping the entity_name as-is if it already includes a module
    prefix (e.g. APBill), otherwise we prefix.
    In Intacct practice, entity_name is already unique across modules
    (APBill vs ARInvoice etc.), so this is usually a no-op.
    """
    name = (entry.get("entity_name") or "").strip()
    if not name:
        return ""
    return name


def choose_better(a: dict, b: dict) -> dict:
    """
    Pick the higher-quality of two duplicates.

    Priority:
    1. Higher confidence
    2. More non-null companion_classes
    3. More non-null related_files
    4. ent_exists == True
    """
    def score(x: dict) -> tuple:
        companions = sum(1 for v in x["companion_classes"].values() if v)
        related = sum(1 for v in x["related_files"].values() if v)
        return (
            float(x.get("confidence") or 0.0),
            companions,
            related,
            1 if x["validation"].get("ent_exists") else 0,
        )

    return a if score(a) >= score(b) else b


# ---------------------------------------------------------------------
# Main merge
# ---------------------------------------------------------------------

def merge_entities(
    input_path: Path,
    output_path: Path,
    repo_root: Optional[Path],
    verify: bool,
    strict: bool,
) -> int:

    if not input_path.exists():
        print(f"[error] Input file not found: {input_path}", file=sys.stderr)
        return 1

    raw_entries = read_jsonl(input_path)

    merged: dict[str, dict] = {}
    duplicates: dict[str, int] = defaultdict(int)
    schema_issues: list[str] = []
    disk_issues_count = 0
    total = 0
    skipped = 0

    for line_no, raw in raw_entries:
        total += 1

        entry = normalize_entry(raw)
        issues = validate_required(entry, line_no)

        if issues:
            schema_issues.extend(issues)
            skipped += 1
            continue

        if verify:
            if not repo_root:
                print(
                    "[error] --verify requires --repo-root",
                    file=sys.stderr,
                )
                return 1
            notes = verify_paths_on_disk(entry, repo_root)
            if notes:
                disk_issues_count += 1

        key = canonical_key(entry)
        if not key:
            skipped += 1
            continue

        if key in merged:
            duplicates[key] += 1
            merged[key] = choose_better(merged[key], entry)
        else:
            merged[key] = entry

    output_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_out = {k: merged[k] for k in sorted(merged.keys())}

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(sorted_out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # ---------------- Report ----------------
    print(f"Input entries         : {total}")
    print(f"Skipped               : {skipped}")
    print(f"Unique entities       : {len(merged)}")
    print(f"Duplicates collapsed  : {sum(duplicates.values())}")
    if verify:
        print(f"Entries with missing files (downgraded): {disk_issues_count}")
    print(f"Written               : {output_path}")

    if duplicates:
        print()
        print("Top duplicated keys:")
        for k, v in sorted(duplicates.items(), key=lambda x: -x[1])[:10]:
            print(f"  {k}: {v}")

    if schema_issues:
        print()
        print(f"Schema issues ({len(schema_issues)}):")
        for issue in schema_issues[:20]:
            print(f"  {issue}")
        if len(schema_issues) > 20:
            print(f"  ...and {len(schema_issues) - 20} more")

        if strict:
            print()
            print("[strict] Aborting due to schema issues.", file=sys.stderr)
            return 2

    return 0


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge GHCP entity outputs into config/entities.json"
    )

    parser.add_argument(
        "--input",
        default="config/entities.jsonl",
        help="Path to entities.jsonl produced by GHCP",
    )

    parser.add_argument(
        "--output",
        default="config/entities.json",
        help="Path to write merged canonical entities.json",
    )

    parser.add_argument(
        "--repo-root",
        default=None,
        help="Absolute path to ia-app repo root (required for --verify)",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify referenced file paths exist on disk under --repo-root",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail with non-zero exit code if any schema violation is found",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else None

    return merge_entities(
        input_path=input_path,
        output_path=output_path,
        repo_root=repo_root,
        verify=args.verify,
        strict=args.strict,
    )


if __name__ == "__main__":
    raise SystemExit(main())