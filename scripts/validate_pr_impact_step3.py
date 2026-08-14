#!/usr/bin/env python3
"""Validate a separately materialized repo-v1 PR impact Step 3 report."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.2"
ANALYSIS_KIND = "pr_impact_step_3"
STATUSES = {"complete", "partial", "empty", "blocked"}
TOP_LEVEL_KEYS = {
    "schema_version",
    "analysis_kind",
    "status",
    "error",
    "input",
    "preflight",
    "changed_files",
    "seed_files",
    "seed_symbols",
    "reached_symbols",
    "transitive_edges",
    "skipped_edges",
    "caller_evidence",
    "entity_context",
    "business_impact",
    "gaps",
    "warnings",
    "provenance",
}
SEED_FILE_STATES = {
    "available",
    "deleted",
    "parser_failed",
    "symbol_less",
    "missing_target_file",
}
ALLOWED_RELATIONSHIP_TYPES = {"CALLS", "STATIC_CALLS"}
ENTITY_MAPPING_GAP = "entity_context:repo_v1_symbol_entity_mapping_not_modelled"
SKIP_REASONS = {
    "non_call_relationship",
    "unresolved_resolution",
    "source_symbol_missing",
    "below_confidence",
}
CALLER_EVIDENCE_REVIEW_REASONS = {
    "below_confidence",
    "unresolved_resolution",
    "source_symbol_missing",
}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_evidence(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return bool(value)
    return True


def _normalized_symbol_kind(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"", "unknown"}:
        return None
    if normalized in {"cqry", "qry"}:
        return "query"
    return str(value)


def _validate_declaration_range(value: Any, errors: list[str], label: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object or null")
        return
    start, end = value.get("start_line"), value.get("end_line")
    if start is None and end is None:
        return
    if not _is_int(start) or not _is_int(end) or start <= 0 or end < start:
        errors.append(f"{label} has invalid declaration range")


def _ids(items: Any, key: str, errors: list[str], label: str) -> set[int]:
    if not isinstance(items, list):
        errors.append(f"{label} must be a list")
        return set()
    result: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"{label} item must be an object")
            continue
        value = item.get(key)
        if not _is_int(value) or value <= 0:
            errors.append(f"{label} item requires a positive {key}")
        elif value in result:
            errors.append(f"duplicate {label} {key}: {value}")
        else:
            result.add(value)
    return result


def _validate_revision_fields(
    item: Mapping[str, Any],
    input_data: Mapping[str, Any],
    errors: list[str],
    label: str,
) -> None:
    target = input_data.get("target_revision")
    if not _nonempty_text(item.get("catalog_source_revision")):
        errors.append(f"{label} requires catalog_source_revision")
    elif item.get("catalog_source_revision") != target:
        errors.append(f"{label} has a stale catalog source revision")
    if not _nonempty_text(item.get("fixture_target_revision")):
        errors.append(f"{label} requires fixture_target_revision")
    elif item.get("fixture_target_revision") != target:
        errors.append(f"{label} has a mismatched fixture target revision")
    if not _is_int(item.get("repository_id")) or item.get("repository_id") <= 0:
        errors.append(f"{label} requires repository_id")
    if not _is_int(item.get("file_id")) or item.get("file_id") <= 0:
        errors.append(f"{label} requires file_id")
    if not _nonempty_text(item.get("file_path")):
        errors.append(f"{label} requires file_path")
    if not _nonempty_text(item.get("blob_object_id")):
        errors.append(f"{label} requires blob_object_id")


def _validate_symbol(
    item: Any, input_data: Mapping[str, Any], errors: list[str], label: str
) -> None:
    if not isinstance(item, dict):
        errors.append(f"{label} must be an object")
        return
    for key in (
        "catalog_record_id",
        "symbol_id",
        "name",
        "kind",
        "language",
        "stable_key",
    ):
        if key not in item or key.endswith("_id") and (not _is_int(item[key]) or item[key] <= 0):
            errors.append(f"{label} requires {key}")
        elif key in {"name", "kind", "language", "stable_key"} and not _nonempty_text(
            item[key]
        ):
            errors.append(f"{label} requires non-empty {key}")
    _validate_revision_fields(item, input_data, errors, label)
    declaration = item.get("declaration_range")
    if (
        not isinstance(declaration, dict)
        or declaration.get("start_line") != item.get("start_line")
        or declaration.get("end_line") != item.get("end_line")
    ):
        errors.append(f"{label} has invalid declaration_range")
    _validate_declaration_range(declaration, errors, f"{label} declaration_range")
    if (item.get("start_line") is None) != (item.get("end_line") is None):
        errors.append(f"{label} has incomplete declaration range")
    elif item.get("start_line") is not None:
        _validate_declaration_range(
            {"start_line": item.get("start_line"), "end_line": item.get("end_line")},
            errors,
            f"{label} declaration range",
        )


def _validate_edge(
    item: Any,
    input_data: Mapping[str, Any],
    errors: list[str],
    label: str,
    *,
    skipped: bool,
) -> None:
    if not isinstance(item, dict):
        errors.append(f"{label} must be an object")
        return
    for key in ("catalog_record_id", "relationship_id", "repository_id", "file_id"):
        if not _is_int(item.get(key)) or item[key] <= 0:
            errors.append(f"{label} requires positive {key}")
    if item.get("catalog_record_id") != item.get("relationship_id"):
        errors.append(f"{label} catalog_record_id must equal relationship_id")
    _validate_revision_fields(item, input_data, errors, label)
    for prefix in ("source", "target"):
        if prefix == "source" and item.get("source_symbol_id") is None:
            continue
        for key in (
            "file_id",
            "file_path",
            "blob_object_id",
            "catalog_source_revision",
        ):
            field = f"{prefix}_{key}"
            value = item.get(field)
            if (key == "file_id" and (not _is_int(value) or value <= 0)) or (key != "file_id" and not _nonempty_text(value)):
                errors.append(f"{label} requires {field}")
        if item.get(f"{prefix}_catalog_source_revision") != input_data.get(
            "target_revision"
        ):
            errors.append(f"{label} has stale {prefix} symbol provenance")
        if not _nonempty_text(item.get(f"{prefix}_symbol_name")):
            errors.append(f"{label} requires {prefix}_symbol_name")
        if not _nonempty_text(item.get(f"{prefix}_symbol_kind")):
            errors.append(f"{label} requires {prefix}_symbol_kind")
        _validate_declaration_range(
            item.get(f"{prefix}_declaration_range"),
            errors,
            f"{label} {prefix}_declaration_range",
        )
    if (
        not isinstance(item.get("relationship_type"), str)
        or not item["relationship_type"]
    ):
        errors.append(f"{label} requires relationship_type")
    elif not skipped and item["relationship_type"] not in ALLOWED_RELATIONSHIP_TYPES:
        errors.append(f"{label} has invalid traversed relationship_type")
    if (
        not isinstance(item.get("resolution_class"), str)
        or not item["resolution_class"]
    ):
        errors.append(f"{label} requires resolution_class")
    elif not skipped and item["resolution_class"] != "project_resolved":
        errors.append(f"{label} has invalid traversed resolution_class")
    elif item["resolution_class"] not in {"project_resolved", "project_unresolved"}:
        errors.append(f"{label} has invalid resolution_class")
    if not _nonempty_text(item.get("resolution_reason")):
        errors.append(f"{label} requires resolution_reason")
    if not _nonempty_text(item.get("extractor")):
        errors.append(f"{label} requires extractor")
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        valid_confidence = False
    elif isinstance(confidence, int):
        valid_confidence = 0 <= confidence <= 1
    else:
        valid_confidence = math.isfinite(confidence) and 0 <= confidence <= 1
    if not valid_confidence:
        errors.append(f"{label} requires confidence between 0 and 1")
    elif skipped and item.get("skip_reason") == "below_confidence":
        threshold = input_data.get("min_confidence")
        if isinstance(threshold, (int, float)) and float(confidence) > float(threshold):
            errors.append(f"{label} below_confidence does not meet min_confidence")
    elif not skipped and isinstance(input_data.get("min_confidence"), (int, float)):
        if float(confidence) <= float(input_data["min_confidence"]):
            errors.append(f"{label} does not exceed min_confidence")
    if not _has_evidence(item.get("evidence")):
        errors.append(f"{label} requires evidence")
    if not _is_int(item.get("target_symbol_id")) or item["target_symbol_id"] <= 0:
        errors.append(f"{label} requires target_symbol_id")
    if item.get("source_symbol_id") is not None and (
        not _is_int(item["source_symbol_id"]) or item["source_symbol_id"] <= 0
    ):
        errors.append(f"{label} has invalid source_symbol_id")
    if (
        not _is_int(item.get("hop"))
        or item["hop"] <= 0
        or not _is_int(input_data.get("max_hops"))
        or item["hop"] > input_data["max_hops"]
    ):
        errors.append(f"{label} requires positive hop")
    if skipped:
        reason = item.get("skip_reason")
        if reason not in SKIP_REASONS:
            errors.append(f"{label} has invalid skip_reason")
        elif (
            reason == "non_call_relationship"
            and item.get("relationship_type") in ALLOWED_RELATIONSHIP_TYPES
        ):
            errors.append(f"{label} non_call_relationship must be non-call")
        elif (
            reason == "unresolved_resolution"
            and item.get("resolution_class") == "project_resolved"
        ):
            errors.append(f"{label} unresolved_resolution must be unresolved")
        elif (
            reason == "source_symbol_missing"
            and item.get("source_symbol_id") is not None
        ):
            errors.append(
                f"{label} source_symbol_missing must have null source_symbol_id"
            )
    elif item.get("edge_status") != "traversed":
        errors.append(f"{label} must have edge_status traversed")


def _validate_edge_symbol_identity(
    edge: Mapping[str, Any],
    symbols_by_id: Mapping[int, Mapping[str, Any]],
    errors: list[str],
    label: str,
) -> None:
    for prefix in ("source", "target"):
        symbol_id = edge.get(f"{prefix}_symbol_id")
        if not _is_int(symbol_id):
            continue
        symbol = symbols_by_id.get(symbol_id)
        if symbol is None:
            continue
        comparisons = {
            f"{prefix}_symbol_name": symbol.get("name"),
            f"{prefix}_symbol_kind": (
                _normalized_symbol_kind(symbol.get("kind"))
                if prefix == "target"
                else symbol.get("kind")
            ),
            f"{prefix}_file_id": symbol.get("file_id"),
            f"{prefix}_file_path": symbol.get("file_path"),
            f"{prefix}_blob_object_id": symbol.get("blob_object_id"),
            f"{prefix}_catalog_source_revision": symbol.get("catalog_source_revision"),
        }
        for field, expected in comparisons.items():
            if edge.get(field) != expected:
                errors.append(f"{label} {field} does not match symbol provenance")
        if edge.get(f"{prefix}_declaration_range") != symbol.get("declaration_range"):
            errors.append(f"{label} {prefix} declaration range does not match symbol")


def validate(report: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be a JSON object"]
    unexpected = sorted(set(report) - TOP_LEVEL_KEYS)
    if unexpected:
        errors.append("unexpected top-level keys: " + ", ".join(unexpected))
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 0.2")
    if report.get("analysis_kind") != ANALYSIS_KIND:
        errors.append("invalid analysis_kind")
    status = report.get("status")
    if status not in STATUSES:
        errors.append("invalid status")
    if status == "blocked":
        error = report.get("error")
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("code"), str)
            or not error.get("code")
            or not isinstance(error.get("message"), str)
            or not error.get("message")
        ):
            errors.append("blocked report requires error.code and error.message")
    elif "error" in report:
        errors.append("error is only allowed for blocked reports")

    required = TOP_LEVEL_KEYS - {"error"}
    for key in sorted(required):
        if key not in report:
            errors.append(f"missing section: {key}")
    input_data = report.get("input")
    if not isinstance(input_data, dict):
        errors.append("input must be an object")
        input_data = {}
    else:
        for key in (
            "fixture",
            "manifest",
            "active_db",
            "repo_key",
            "repo_root",
            "base_revision",
            "target_revision",
        ):
            if status != "blocked" and (
                not isinstance(input_data.get(key), str) or not input_data[key]
            ):
                errors.append(f"input requires {key}")
        if (status != "blocked" or "seed_basis" in input_data) and input_data.get(
            "seed_basis"
        ) != "target_file_all_symbols":
            errors.append("input.seed_basis must be target_file_all_symbols")
        if (status != "blocked" or "max_hops" in input_data) and (
            not _is_int(input_data.get("max_hops"))
            or input_data["max_hops"] not in (1, 2)
        ):
            errors.append("input.max_hops must be 1 or 2")
        confidence = input_data.get("min_confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            errors.append("input.min_confidence must be between 0 and 1")
    preflight = report.get("preflight")
    if status != "blocked":
        if not isinstance(preflight, dict):
            errors.append("preflight must be an object")
        else:
            for key in (
                "build_id",
                "repo_key",
                "target_revision",
                "catalog_revision",
                "revision_relation",
                "compatibility_evidence",
                "integrity_check",
                "foreign_key_check",
            ):
                if key not in preflight:
                    errors.append(f"preflight requires {key}")
            if preflight.get("revision_relation") != "exact":
                errors.append("preflight revision_relation must be exact")
            if preflight.get("catalog_revision") != preflight.get(
                "target_revision"
            ) or preflight.get("target_revision") != input_data.get("target_revision"):
                errors.append(
                    "preflight revisions must exactly match the fixture target"
                )
            if (
                preflight.get("integrity_check") != "ok"
                or preflight.get("foreign_key_check") != "ok"
            ):
                errors.append("preflight integrity/FK checks must be exact")

    changed = report.get("changed_files")
    changed_paths: set[str] = set()
    if not isinstance(changed, list):
        errors.append("changed_files must be a list")
    else:
        for item in changed:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not item["path"]
            ):
                errors.append("changed file requires path")
                continue
            if item["path"] in changed_paths:
                errors.append("changed_files contains duplicate paths")
            changed_paths.add(item["path"])
            if item.get("status") not in {
                "added",
                "modified",
                "deleted",
                "renamed",
                "copied",
            }:
                errors.append("changed file has invalid status")

    seed_files = report.get("seed_files")
    seed_paths: set[str] = set()
    if not isinstance(seed_files, list):
        errors.append("seed_files must be a list")
        seed_files = []
    for item in seed_files:
        if not isinstance(item, dict):
            errors.append("seed file must be an object")
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            errors.append("seed file requires path")
        elif path in seed_paths:
            errors.append("seed_files contains duplicate paths")
        else:
            seed_paths.add(path)
        if item.get("state") not in SEED_FILE_STATES:
            errors.append("seed file has invalid state")
        if item.get("state") == "available":
            for key in (
                "catalog_record_id",
                "file_id",
                "blob_object_id",
                "catalog_source_revision",
                "fixture_target_revision",
            ):
                if key not in item:
                    errors.append(f"available seed file requires {key}")
            for key in ("catalog_record_id", "file_id"):
                if not _is_int(item.get(key)) or item[key] <= 0:
                    errors.append(f"available seed file requires positive {key}")
            if not _nonempty_text(item.get("blob_object_id")):
                errors.append("available seed file requires blob_object_id")
            if not _nonempty_text(item.get("catalog_source_revision")):
                errors.append("available seed file requires catalog_source_revision")
            if not _nonempty_text(item.get("fixture_target_revision")):
                errors.append("available seed file requires fixture_target_revision")
            if item.get("catalog_record_id") != item.get("file_id"):
                errors.append("seed file catalog_record_id must equal file_id")
            if item.get("catalog_source_revision") != input_data.get("target_revision"):
                errors.append("available seed file has a stale catalog source revision")
            if item.get("fixture_target_revision") != input_data.get("target_revision"):
                errors.append(
                    "available seed file has a mismatched fixture target revision"
                )
    if status != "blocked" and seed_paths != changed_paths:
        errors.append("seed_files must have one entry for every changed path")

    seed_symbols = report.get("seed_symbols")
    seed_ids = _ids(seed_symbols, "symbol_id", errors, "seed_symbols")
    if isinstance(seed_symbols, list):
        for item in seed_symbols:
            _validate_symbol(item, input_data, errors, "seed symbol")
            if isinstance(item, dict) and item.get("catalog_record_id") != item.get(
                "symbol_id"
            ):
                errors.append("seed symbol catalog_record_id must equal symbol_id")
    reached_symbols = report.get("reached_symbols")
    reached_ids = _ids(reached_symbols, "symbol_id", errors, "reached_symbols")
    if seed_ids & reached_ids:
        errors.append("seed and reached symbols must not duplicate IDs")
    max_hops = input_data.get("max_hops")
    if isinstance(reached_symbols, list):
        for item in reached_symbols:
            _validate_symbol(item, input_data, errors, "reached symbol")
            if not isinstance(item, dict):
                continue
            if item.get("catalog_record_id") != item.get("symbol_id"):
                errors.append("reached symbol catalog_record_id must equal symbol_id")
            hop = item.get("minimum_hop")
            if (
                not _is_int(hop)
                or not isinstance(max_hops, int)
                or not 1 <= hop <= max_hops
            ):
                errors.append("reached symbol has invalid minimum_hop")
            edge_ids = item.get("contributing_edge_ids")
            if (
                not isinstance(edge_ids, list)
                or not edge_ids
                or any(not _is_int(value) or value <= 0 for value in edge_ids)
            ) or len(edge_ids) != len(set(edge_ids)):
                errors.append("reached symbol has invalid contributing_edge_ids")

    transitive = report.get("transitive_edges")
    skipped = report.get("skipped_edges")
    transitive_ids = _ids(transitive, "relationship_id", errors, "transitive_edges")
    skipped_ids = _ids(skipped, "relationship_id", errors, "skipped_edges")
    if transitive_ids & skipped_ids:
        errors.append(
            "transitive and skipped edges must not duplicate relationship IDs"
        )
    if isinstance(transitive, list):
        for item in transitive:
            _validate_edge(item, input_data, errors, "transitive edge", skipped=False)
    if isinstance(skipped, list):
        for item in skipped:
            _validate_edge(item, input_data, errors, "skipped edge", skipped=True)
    caller_evidence = report.get("caller_evidence")
    if not isinstance(caller_evidence, dict):
        errors.append("caller_evidence must be an object")
    else:
        caller_status = caller_evidence.get("status")
        if caller_status not in {"complete", "needs_review", "empty", "blocked"}:
            errors.append("caller_evidence has invalid status")
        if caller_evidence.get("traversed_edge_count") != len(
            transitive if isinstance(transitive, list) else []
        ):
            errors.append("caller_evidence traversed_edge_count is inconsistent")
        if caller_evidence.get("reached_symbol_count") != len(
            reached_symbols if isinstance(reached_symbols, list) else []
        ):
            errors.append("caller_evidence reached_symbol_count is inconsistent")
        counts = caller_evidence.get("skipped_edge_counts")
        if not isinstance(counts, dict):
            errors.append("caller_evidence skipped_edge_counts must be an object")
            counts = {}
        for reason, count in counts.items():
            if reason not in SKIP_REASONS:
                errors.append("caller_evidence has an invalid skipped edge reason")
            if not _is_int(count) or count <= 0:
                errors.append("caller_evidence skipped edge counts must be positive integers")
        actual_counts: dict[str, int] = {}
        for edge in skipped if isinstance(skipped, list) else []:
            if isinstance(edge, dict) and isinstance(edge.get("skip_reason"), str):
                reason = edge["skip_reason"]
                actual_counts[reason] = actual_counts.get(reason, 0) + 1
        if counts != actual_counts:
            errors.append("caller_evidence skipped_edge_counts is inconsistent")
        review_required = bool(
            CALLER_EVIDENCE_REVIEW_REASONS & set(actual_counts)
        )
        expected_status = (
            "blocked"
            if status == "blocked"
            else "empty"
            if status == "empty"
            else "needs_review"
            if review_required
            else "complete"
        )
        if caller_status != expected_status:
            errors.append("caller_evidence status does not reflect skipped evidence")
    all_edge_ids = transitive_ids | skipped_ids
    minimum_hops = {
        item.get("symbol_id"): item.get("minimum_hop")
        for item in reached_symbols
        if isinstance(item, dict)
    }
    for edge_list, label in (
        (transitive, "transitive edge"),
        (skipped, "skipped edge"),
    ):
        if not isinstance(edge_list, list):
            continue
        for edge in edge_list:
            if not isinstance(edge, dict):
                continue
            hop = edge.get("hop")
            target_id = edge.get("target_symbol_id")
            valid_targets = (
                seed_ids
                if hop == 1
                else {
                    symbol_id
                    for symbol_id, minimum_hop in minimum_hops.items()
                    if minimum_hop == hop - 1
                }
            )
            if target_id not in valid_targets:
                errors.append(f"{label} target does not match its frontier hop")
            if label == "transitive edge" and edge.get("source_symbol_id") is None:
                errors.append("transitive edge requires source_symbol_id")
            if label == "transitive edge":
                source_id = edge.get("source_symbol_id")
                source_hop = 0 if source_id in seed_ids else minimum_hops.get(source_id)
                if source_hop is None:
                    errors.append("transitive edge source is not a reached symbol")
                elif source_hop > hop:
                    errors.append(
                        "transitive edge source was first reached after its edge hop"
                    )
                elif source_hop == hop:
                    reached = next(
                        (
                            item
                            for item in reached_symbols
                            if isinstance(item, dict)
                            and item.get("symbol_id") == source_id
                        ),
                        None,
                    )
                    if not isinstance(reached, dict) or edge.get(
                        "relationship_id"
                    ) not in reached.get("contributing_edge_ids", []):
                        errors.append(
                            "newly reached source must list the edge as contributing"
                        )
    symbols_by_id = {
        item.get("symbol_id"): item
        for item in [
            *(seed_symbols if isinstance(seed_symbols, list) else []),
            *(reached_symbols if isinstance(reached_symbols, list) else []),
        ]
        if isinstance(item, dict) and _is_int(item.get("symbol_id"))
    }
    for edge_list, label in (
        (transitive, "transitive edge"),
        (skipped, "skipped edge"),
    ):
        if isinstance(edge_list, list):
            for edge in edge_list:
                if isinstance(edge, dict):
                    _validate_edge_symbol_identity(edge, symbols_by_id, errors, label)
    if isinstance(reached_symbols, list):
        by_id = {
            item.get("symbol_id"): item
            for item in reached_symbols
            if isinstance(item, dict)
        }
        for symbol_id, item in by_id.items():
            for edge_id in item.get("contributing_edge_ids", []):
                if edge_id not in transitive_ids:
                    errors.append("contributing edge must be a transitive edge")
                else:
                    edge = next(
                        edge
                        for edge in transitive
                        if edge.get("relationship_id") == edge_id
                    )
                    if edge.get("source_symbol_id") != symbol_id or edge.get(
                        "hop"
                    ) != item.get("minimum_hop"):
                        errors.append(
                            "contributing edge is inconsistent with minimum_hop"
                        )

    entity = report.get("entity_context")
    old_entity = {
        "status": "unavailable",
        "reason": "repo_v1_symbol_entity_mapping_not_modelled",
        "mappings": [],
        "unavailable_symbol_ids": sorted(seed_ids | reached_ids),
    }
    if entity != old_entity:
        if not isinstance(entity, dict) or entity.get("status") not in {
            "available",
            "partial",
        }:
            errors.append("entity_context does not match the reviewed mapping shape")
        else:
            if not _nonempty_text(entity.get("reason")) or not isinstance(
                entity.get("mappings"), list
            ):
                errors.append("entity_context requires reason and mappings")
            unavailable = entity.get("unavailable_symbol_ids")
            if not isinstance(unavailable, list) or any(
                not _is_int(value) for value in unavailable
            ):
                errors.append(
                    "entity_context unavailable_symbol_ids must be integer list"
                )
            for mapping in entity.get("mappings", []):
                if not isinstance(mapping, dict):
                    errors.append("entity mapping must be an object")
                    continue
                for key in (
                    "symbol_id",
                    "mapping_type",
                    "resolution_status",
                    "resolution_reason",
                    "mapping_contract_path",
                    "mapping_contract_sha256",
                    "target_revision",
                    "contract_entry_key",
                    "extractor",
                ):
                    if key == "symbol_id" and (
                        not _is_int(mapping.get(key)) or mapping[key] <= 0
                    ):
                        errors.append("entity mapping requires positive symbol_id")
                    elif key != "symbol_id" and not _nonempty_text(mapping.get(key)):
                        errors.append(f"entity mapping requires {key}")
                if mapping.get("target_revision") != input_data.get("target_revision"):
                    errors.append("entity mapping target revision is stale")
                if mapping.get("resolution_status") == "resolved":
                    for key in ("entity_occurrence_id", "entity_id", "entity_name"):
                        if key.endswith("_id") and (
                            not _is_int(mapping.get(key)) or mapping[key] <= 0
                        ):
                            errors.append(f"resolved entity mapping requires {key}")
                        elif key == "entity_name" and not _nonempty_text(
                            mapping.get(key)
                        ):
                            errors.append(
                                "resolved entity mapping requires entity_name"
                            )
    business = report.get("business_impact")
    if business != {
        "status": "deferred",
        "reason": "transitive callers are verified code evidence only",
        "facts": [],
    }:
        errors.append("business_impact does not match the fixed deferred shape")
    for key in ("gaps", "warnings"):
        if not isinstance(report.get(key), list) or any(
            not isinstance(item, str) for item in report[key]
        ):
            errors.append(f"{key} must be a list of strings")
    if (
        status != "blocked"
        and entity == old_entity
        and ENTITY_MAPPING_GAP not in report.get("gaps", [])
    ):
        errors.append("non-blocked reports require the entity mapping gap")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    elif status != "blocked":
        if provenance.get("catalog_source_revision") != input_data.get(
            "target_revision"
        ):
            errors.append("provenance catalog source revision is stale")
        if provenance.get("fixture_target_revision") != input_data.get(
            "target_revision"
        ):
            errors.append("provenance fixture target revision is mismatched")

    if status == "blocked" and (
        seed_symbols or reached_symbols or transitive or skipped
    ):
        errors.append("blocked reports must not contain traversal rows")
    if status == "empty" and (
        seed_ids
        or reached_ids
        or all_edge_ids
        or not seed_files
        or any(
            item.get("state") != "symbol_less"
            for item in seed_files
            if isinstance(item, dict)
        )
    ):
        errors.append(
            "empty reports require only symbol_less seed files and no traversal rows"
        )
    if status == "complete":
        if isinstance(skipped, list) and any(
            item.get("state") != "available"
            for item in seed_files
            if isinstance(item, dict)
        ):
            errors.append("complete reports require available seed files")
        if isinstance(skipped, list) and any(
            item.get("skip_reason") != "non_call_relationship"
            for item in skipped
            if isinstance(item, dict)
        ):
            errors.append("complete reports cannot contain attributable skipped edges")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"invalid report: {exc}", file=sys.stderr)
        return 2
    errors = validate(report)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
