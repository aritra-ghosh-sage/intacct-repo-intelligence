"""Deterministic evidence prioritization for PR impact reports."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _fact_key(surface: str, fact: dict[str, Any]) -> str:
    if fact.get("catalog_record_id") is not None:
        return f"{surface}:{fact['catalog_record_id']}"
    if fact.get("fact_key"):
        return f"{surface}:{fact['fact_key']}"
    return f"{surface}:{fact.get('source_path', '')}:{fact.get('source_location', '')}:{fact.get('evidence', '')}"


def rank_direct_traces(
    direct_traces: list[dict[str, Any]],
    changed_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changed_paths = {
        str(item.get("path"))
        for item in changed_files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"surfaces": set(), "fact_keys": set(), "statuses": set()}
    )
    for trace in direct_traces:
        if not isinstance(trace, dict) or trace.get("status") in {"unavailable", "deferred"}:
            continue
        surface = trace.get("surface")
        if not isinstance(surface, str):
            continue
        for fact in trace.get("facts", []):
            if not isinstance(fact, dict):
                continue
            source_path = fact.get("source_path")
            if not isinstance(source_path, str) or not source_path:
                continue
            item = grouped[source_path]
            item["surfaces"].add(surface)
            item["fact_keys"].add(_fact_key(surface, fact))
            item["statuses"].add(trace.get("status"))

    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            -len(item[1]["surfaces"]),
            -len(item[1]["fact_keys"]),
            -(item[0] in changed_paths),
            item[0],
        ),
    )
    return [
        {
            "rank": index,
            "source_path": source_path,
            "distinct_surface_count": len(value["surfaces"]),
            "fact_count": len(value["fact_keys"]),
            "changed_file": source_path in changed_paths,
            "surfaces": sorted(value["surfaces"]),
            "fact_keys": sorted(value["fact_keys"]),
            "statuses": sorted(value["statuses"]),
        }
        for index, (source_path, value) in enumerate(ordered, start=1)
    ]
