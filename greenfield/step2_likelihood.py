"""Deterministic source anchors and likely-test ranking for Greenfield Step 2."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

RULE_SET_VERSION = "0.1"
MAX_LIKELY_TESTS = 25


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _source_path(evidence: Mapping[str, Any]) -> str | None:
    return _text(evidence.get("source_path"))


def _interface_id(node: Mapping[str, Any]) -> str | None:
    if node.get("kind") != "api_object":
        return None
    identity = _text(node.get("identity"))
    return f"api_object:{identity}" if identity else None


def _symbol_name(node: Mapping[str, Any]) -> str | None:
    if node.get("kind") != "php_symbol":
        return None
    name = _text(node.get("name"))
    if not name:
        return None
    parent = _text(node.get("parent_symbol"))
    return f"{parent}:{name}" if parent else name


def _evidence_key(value: Mapping[str, Any]) -> tuple[str, int, int, str]:
    lines = value.get("source_lines", {})
    return (
        str(value.get("source_path", "")),
        int(lines.get("start", 0)) if isinstance(lines, Mapping) else 0,
        int(lines.get("end", 0)) if isinstance(lines, Mapping) else 0,
        str(value.get("fact", "")),
    )


def build_source_anchors(
    changed_paths: Iterable[str], semantic_indexes: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Join changed source evidence to API objects through exact entity edges."""

    changed = set(changed_paths)
    anchors: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for index in semantic_indexes:
        nodes = {
            node.get("key"): node
            for node in index.get("nodes", [])
            if isinstance(node, Mapping) and isinstance(node.get("key"), str)
        }
        entity_to_interfaces: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        for edge in index.get("edges", []):
            if not isinstance(edge, Mapping) or edge.get("kind") != "api_object_entity":
                continue
            interface_node = nodes.get(edge.get("source"))
            interface_id = (
                _interface_id(interface_node)
                if isinstance(interface_node, Mapping)
                else None
            )
            target = _text(edge.get("target"))
            if interface_id and target:
                for evidence in edge.get("evidence", []):
                    if isinstance(evidence, Mapping):
                        entity_to_interfaces.setdefault(target, []).append(
                            (interface_id, evidence)
                        )

        for edge in index.get("edges", []):
            if not isinstance(edge, Mapping):
                continue
            target = _text(edge.get("target"))
            if not target or not target.startswith("entity:"):
                continue
            source_node = nodes.get(edge.get("source"))
            if not isinstance(source_node, Mapping):
                continue
            for evidence in edge.get("evidence", []):
                if not isinstance(evidence, Mapping):
                    continue
                path = _source_path(evidence)
                if path not in changed:
                    continue
                source_lines = evidence.get("source_lines")
                if not isinstance(source_lines, Mapping):
                    source_lines = {}
                source_symbol = _symbol_name(source_node)
                entity = target.removeprefix("entity:")
                interface_rows = entity_to_interfaces.get(target, [])
                key = (path or "", source_symbol or "", entity, str(source_lines))
                anchor = anchors.setdefault(
                    key,
                    {
                        "source_path": path,
                        "source_symbol": source_symbol,
                        "source_lines": dict(source_lines),
                        "entity": entity,
                        "source_revision": index.get("revision"),
                        "interfaces": [],
                        "evidence": [],
                    },
                )
                anchor["evidence"].append(dict(evidence))
                for interface_id, interface_evidence in interface_rows:
                    mapping = {
                        "interface_id": interface_id,
                        "mapping_kind": "semantic_source_contract",
                        "source_revision": index.get("revision"),
                        "evidence": [dict(evidence), dict(interface_evidence)],
                    }
                    if mapping not in anchor["interfaces"]:
                        anchor["interfaces"].append(mapping)

    for anchor in anchors.values():
        anchor["evidence"] = sorted(
            anchor["evidence"], key=_evidence_key
        )
        anchor["interfaces"] = sorted(
            anchor["interfaces"],
            key=lambda item: (item["interface_id"], item["mapping_kind"]),
        )
    return sorted(
        anchors.values(),
        key=lambda item: (
            str(item.get("source_path")),
            str(item.get("source_symbol")),
            str(item.get("entity")),
            str(item.get("source_lines")),
        ),
    )


def attach_explicit_contract_anchors(
    anchors: list[dict[str, Any]],
    changed_paths: Iterable[str],
    relations: Iterable[Mapping[str, Any]],
    revision: str,
) -> list[dict[str, Any]]:
    """Add exact contract mappings without replacing semantic evidence."""

    result = [dict(anchor) for anchor in anchors]
    by_path = {str(anchor.get("source_path")): anchor for anchor in result}
    for relation in relations:
        if relation.get("status") != "active":
            continue
        interface_id = _text(relation.get("interface_id"))
        if not interface_id:
            continue
        for path in sorted(set(changed_paths) & set(relation.get("source_paths", []))):
            declared_symbols = {
                str(value) for value in relation.get("source_symbols", [])
            }
            matching = [
                value
                for value in result
                if value.get("source_path") == path
                and (
                    not declared_symbols
                    or value.get("source_symbol") in declared_symbols
                )
            ]
            if matching:
                target_anchors = matching
            else:
                target_anchors = [by_path.get(path)]
            for anchor in target_anchors:
                if anchor is None:
                    anchor = {
                        "source_path": path,
                        "source_symbol": (
                            sorted(declared_symbols)[0]
                            if relation.get("relationship_type") == "behavior_contract" and declared_symbols
                            else next(iter(declared_symbols), None)
                        ),
                        "source_lines": {},
                        "entity": None,
                        "source_revision": revision,
                        "interfaces": [],
                        "evidence": [],
                    }
                    result.append(anchor)
                    by_path[path] = anchor
                mapping = {
                    "interface_id": interface_id,
                    "mapping_kind": "explicit_source_contract",
                    "source_revision": revision,
                    "evidence": [],
                }
                if mapping not in anchor["interfaces"]:
                    anchor["interfaces"].append(mapping)
    return sorted(
        result,
        key=lambda item: (
            str(item.get("source_path")),
            str(item.get("source_symbol")),
            str(item.get("entity")),
        ),
    )


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[a-z0-9]+", value)
        if len(token) > 2
    }


def _test_surface(path: str) -> bool:
    return bool(
        re.search(
            r"(?:^|/)(?:features?|tests?|testdefinitions|testscripts|specs?)(?:/|$)|\.(?:feature|feature\.xml|jmx|test\.xml)$",
            path,
            re.IGNORECASE,
        )
    )


def rank_likely_tests(
    candidate: Mapping[str, Any],
    inventory: Mapping[str, Any] | None,
    anchors: Iterable[Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank inventory paths using contract and source-backed signals only."""

    if not isinstance(inventory, Mapping) or inventory.get("status") != "available":
        return []
    paths = sorted(
        path
        for path in inventory.get("inventory_paths", [])
        if isinstance(path, str) and _test_surface(path)
    )
    if not paths:
        return []

    interface_id = str(candidate.get("interface_id", ""))
    candidate_interfaces = {interface_id} if not interface_id.startswith("repository:") else set()
    anchor_rows = list(anchors)
    for anchor in anchor_rows:
        for mapping in anchor.get("interfaces", []):
            if isinstance(mapping, Mapping) and isinstance(mapping.get("interface_id"), str):
                candidate_interfaces.add(mapping["interface_id"])
    entities = {
        str(anchor.get("entity", ""))
        for anchor in anchor_rows
        if anchor.get("entity")
    }
    relevant_relations = [
        relation
        for relation in relations
        if relation.get("status") == "active"
        and relation.get("interface_id") in candidate_interfaces
    ]
    obligations = {
        str(obligation.get("path")): obligation
        for relation in relevant_relations
        for obligation in relation.get("test_obligations", [])
        if isinstance(obligation, Mapping) and isinstance(obligation.get("path"), str)
    }

    scored: list[dict[str, Any]] = []
    for path in paths:
        reasons: list[str] = []
        evidence: list[dict[str, Any]] = []
        score = 0
        if path in obligations:
            score = 100
            reasons.append("exact_test_obligation")
            evidence.append({"kind": "contract_test_obligation", "path": path})
        else:
            path_tokens = _tokens(path)
            interface_tokens = set().union(*(_tokens(value) for value in candidate_interfaces))
            entity_tokens = set().union(*(_tokens(value) for value in entities)) if entities else set()
            interface_overlap = path_tokens & interface_tokens
            entity_overlap = path_tokens & entity_tokens
            if interface_overlap:
                score += min(60, 25 * len(interface_overlap))
                reasons.append("interface_path_token_overlap")
                evidence.append({"kind": "source_interface", "interfaces": sorted(candidate_interfaces)})
            if entity_overlap:
                score += min(25, 15 * len(entity_overlap))
                reasons.append("source_entity_path_token_overlap")
                evidence.append({"kind": "source_entity", "entities": sorted(entities)})
            if score == 0:
                continue
            score = min(score, 85)
        confidence = "high" if score >= 80 else "medium" if score >= 50 else "low"
        scored.append(
            {
                "path": path,
                "score": score,
                "score_rule_set_version": RULE_SET_VERSION,
                "confidence": confidence,
                "reasons": sorted(reasons),
                "evidence": evidence,
                "basis": "contract_backed" if score == 100 else "source_ranked",
            }
        )
    return sorted(scored, key=lambda item: (-item["score"], item["path"]))[:MAX_LIKELY_TESTS]
