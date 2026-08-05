"""Strict, source-owned Contract-V1 inputs for REST automation coverage.

Contract-V0 is the archived, disabled-suite compatibility path and continues to
use the legacy ``object-mapping.json`` reader.  Contract-V1 deliberately has a
separate, closed JSON surface: it never infers an alias, version bridge, or
non-request step from any other target-owned file.

The three Contract-V1 documents have these exact envelopes:

* object mapping: ``{"contract_version": 1, "mappings": [...]}``
* version compatibility: ``{"contract_version": 1, "bridges": [...]}``
* non-request inventory: ``{"contract_version": 1, "entries": [...]}``

``mappings`` entries are ``token``, ``path``, and ``coverage_scope``;
``bridges`` entries are ``test_version`` and ``endpoint_version``; and
``entries`` are ``text`` and lowercase hexadecimal ``sha256``.  Lists and
object keys must already be in canonical lexical order.  Keeping this policy
here makes the target-owned source audit deterministic and prevents later
extraction stages from accepting a looser, incompatible dialect.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_V0 = 0
CONTRACT_V1 = 1
CONTRACT_V1_PATH_FIELDS = (
    "features_root",
    "object_mapping",
    "version_compatibility",
    "non_request_inventory",
)

# Contract-V1 was originally implemented with three documents owned by the
# automation checkout.  Those documents are deliberately no longer evidence
# inputs.  Keep their parser below only so an old catalog can be diagnosed;
# new coverage uses this checked-in map instead.
STATIC_MAP_PATH = Path(__file__).with_name("rest_automation_static_map.json")
STATIC_MAP_VERSION = 1

_VERSION = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_TOKEN = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RestAutomationContractError(ValueError):
    """Raised when a Contract-V1 input is absent, malformed, or noncanonical."""


@dataclass(frozen=True)
class ContractV1Paths:
    """Resolved, manifest-owned Contract-V1 evidence files."""

    features_root: Path
    object_mapping: Path
    version_compatibility: Path
    non_request_inventory: Path

    def files(self) -> tuple[Path, Path, Path]:
        return (
            self.object_mapping,
            self.version_compatibility,
            self.non_request_inventory,
        )


@dataclass(frozen=True)
class ContractAudit:
    """Stable audit record for one Contract-V1 source artifact."""

    field: str
    path: str
    sha256: str


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RestAutomationContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except OSError as exc:
        raise RestAutomationContractError(f"cannot read contract file {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise RestAutomationContractError(f"contract file is not UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RestAutomationContractError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise RestAutomationContractError(f"contract file {path} must be a JSON object")
    return payload


def _closed_object(
    payload: dict[str, Any], allowed: tuple[str, ...], context: str
) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise RestAutomationContractError(
            f"{context} contains unknown field{'s' if len(unknown) > 1 else ''}: "
            + ", ".join(unknown)
        )
    missing = [key for key in allowed if key not in payload]
    if missing:
        raise RestAutomationContractError(
            f"{context} is missing required field{'s' if len(missing) > 1 else ''}: "
            + ", ".join(missing)
        )
    if tuple(payload) != tuple(sorted(payload)):
        raise RestAutomationContractError(f"{context} keys must be in lexical order")


def _canonical_text(value: Any, pattern: re.Pattern[str], context: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RestAutomationContractError(f"{context} is not canonical")
    return value


def _canonical_path(value: Any, scope: str, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise RestAutomationContractError(f"{context} must be a non-empty string")
    if value != value.strip() or not value.startswith("/") or "?" in value or "#" in value:
        raise RestAutomationContractError(f"{context} is not canonical")
    if "//" in value or "/./" in value or "/../" in value or value.endswith(("/.", "/..")):
        raise RestAutomationContractError(f"{context} is not canonical")
    if scope == "endpoint":
        if not value.startswith("/objects/") or not value.removeprefix("/objects/"):
            raise RestAutomationContractError(
                f"{context} endpoint scope requires a canonical /objects/... route"
            )
    elif scope == "non_endpoint" and not value.startswith(
        ("/services/", "/workflows/")
    ):
        raise RestAutomationContractError(
            f"{context} non_endpoint scope requires a /services/... or /workflows/... route"
        )
    return value


def _validate_envelope(payload: dict[str, Any], collection: str, context: str) -> list[Any]:
    _closed_object(payload, ("contract_version", collection), context)
    if type(payload["contract_version"]) is not int or payload["contract_version"] != CONTRACT_V1:
        raise RestAutomationContractError(f"{context}.contract_version must be the integer 1")
    entries = payload[collection]
    if not isinstance(entries, list):
        raise RestAutomationContractError(f"{context}.{collection} must be a list")
    return entries


def load_object_mapping_contract(path: Path) -> list[dict[str, str]]:
    """Read a closed Contract-V1 token-to-route mapping document."""
    entries = _validate_envelope(_load_json(path), "mappings", "object_mapping")
    result: list[dict[str, str]] = []
    previous: str | None = None
    for index, raw in enumerate(entries):
        context = f"object_mapping.mappings[{index}]"
        if not isinstance(raw, dict):
            raise RestAutomationContractError(f"{context} must be an object")
        _closed_object(raw, ("token", "path", "coverage_scope"), context)
        token = _canonical_text(raw["token"], _TOKEN, f"{context}.token")
        scope = raw["coverage_scope"]
        if scope not in {"endpoint", "non_endpoint"}:
            raise RestAutomationContractError(f"{context}.coverage_scope is not canonical")
        path_value = _canonical_path(raw["path"], scope, f"{context}.path")
        if previous is not None and token <= previous:
            raise RestAutomationContractError("object_mapping.mappings must be sorted by token")
        previous = token
        result.append({"token": token, "path": path_value, "coverage_scope": scope})
    return result


def load_version_compatibility_contract(path: Path) -> list[dict[str, str]]:
    """Read closed, explicit Contract-V1 version bridges."""
    entries = _validate_envelope(
        _load_json(path), "bridges", "version_compatibility"
    )
    result: list[dict[str, str]] = []
    previous: tuple[str, str] | None = None
    for index, raw in enumerate(entries):
        context = f"version_compatibility.bridges[{index}]"
        if not isinstance(raw, dict):
            raise RestAutomationContractError(f"{context} must be an object")
        _closed_object(raw, ("test_version", "endpoint_version"), context)
        test_version = _canonical_text(
            raw["test_version"], _VERSION, f"{context}.test_version"
        )
        endpoint_version = _canonical_text(
            raw["endpoint_version"], _VERSION, f"{context}.endpoint_version"
        )
        key = (test_version, endpoint_version)
        if previous is not None and key <= previous:
            raise RestAutomationContractError(
                "version_compatibility.bridges must be sorted and unique"
            )
        previous = key
        result.append({"test_version": test_version, "endpoint_version": endpoint_version})
    return result


def load_non_request_inventory_contract(path: Path) -> list[dict[str, str]]:
    """Read hash-bound Contract-V1 non-request executable steps."""
    entries = _validate_envelope(
        _load_json(path), "entries", "non_request_inventory"
    )
    result: list[dict[str, str]] = []
    previous: str | None = None
    for index, raw in enumerate(entries):
        context = f"non_request_inventory.entries[{index}]"
        if not isinstance(raw, dict):
            raise RestAutomationContractError(f"{context} must be an object")
        _closed_object(raw, ("text", "sha256"), context)
        text = raw["text"]
        if not isinstance(text, str) or not text or text != text.strip():
            raise RestAutomationContractError(f"{context}.text is not canonical")
        digest = _canonical_text(raw["sha256"], _SHA256, f"{context}.sha256")
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != expected:
            raise RestAutomationContractError(f"{context}.sha256 does not bind text")
        if previous is not None and text <= previous:
            raise RestAutomationContractError(
                "non_request_inventory.entries must be sorted and unique by text"
            )
        previous = text
        result.append({"text": text, "sha256": digest})
    return result


def _resolve_contract_path(root: Path, value: Any, field: str, directory: bool) -> Path:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RestAutomationContractError(f"{field} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RestAutomationContractError(f"{field} must be a relative in-root path")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise RestAutomationContractError(f"{field} must stay inside suite_root")
    if directory and not resolved.is_dir():
        raise RestAutomationContractError(f"{field} directory does not exist: {resolved}")
    if not directory and not resolved.is_file():
        raise RestAutomationContractError(f"{field} file does not exist: {resolved}")
    return resolved


def resolve_contract_v1_paths(config: dict[str, Any], suite_root: Path) -> ContractV1Paths:
    """Resolve and parse every Contract-V1 input before extraction may start."""
    root = suite_root.expanduser().resolve()
    if not root.is_dir():
        raise RestAutomationContractError(f"suite_root directory does not exist: {root}")
    resolved = {
        field: _resolve_contract_path(
            root, config.get(field), field, field == "features_root"
        )
        for field in CONTRACT_V1_PATH_FIELDS
    }
    paths = ContractV1Paths(**resolved)
    # Validate all target-owned JSON before reporting a source audit as valid.
    load_object_mapping_contract(paths.object_mapping)
    load_version_compatibility_contract(paths.version_compatibility)
    load_non_request_inventory_contract(paths.non_request_inventory)
    return paths


def audit_contract_v1(paths: ContractV1Paths) -> tuple[ContractAudit, ...]:
    """Return deterministic hashes only after strict input validation succeeds."""
    validators: tuple[tuple[str, Path, Callable[[Path], object]], ...] = (
        ("object_mapping", paths.object_mapping, load_object_mapping_contract),
        (
            "version_compatibility",
            paths.version_compatibility,
            load_version_compatibility_contract,
        ),
        (
            "non_request_inventory",
            paths.non_request_inventory,
            load_non_request_inventory_contract,
        ),
    )
    return tuple(
        ContractAudit(
            field=field,
            path=path.as_posix(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for field, path, validate in validators
        if validate(path) is not None
    )


@dataclass(frozen=True)
class StaticMapEntry:
    """A reviewed, catalog-owned assertion for one exact request token."""

    target_repo: str
    token: str
    revision: str
    method: str
    route: str
    path_spec: str
    registry_release: str
    registry_module: str
    registry_kind: str
    registry_path: str
    entity: str | None
    entity_evidence_file: str | None
    ref_chain: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.target_repo, self.token, self.revision)

    def provenance(self) -> dict[str, Any]:
        return {
            "entity": (
                None
                if self.entity is None
                else {
                    "evidence_file": self.entity_evidence_file,
                    "ref_chain": list(self.ref_chain),
                    "stem": self.entity,
                }
            ),
            "method": self.method,
            "path_spec": self.path_spec,
            "registry": {
                "kind": self.registry_kind,
                "module": self.registry_module,
                "path": self.registry_path,
                "release": self.registry_release,
            },
            "revision": self.revision,
            "route": self.route,
            "target_repo": self.target_repo,
            "token": self.token,
        }


def _relative_source(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RestAutomationContractError(f"{context} must be a non-empty relative source path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
        raise RestAutomationContractError(f"{context} must be a relative source path")
    return candidate.as_posix()


def load_static_map(path: Path = STATIC_MAP_PATH) -> list[StaticMapEntry]:
    """Load the closed, sorted catalog-owned Contract-V1 evidence map."""
    payload = _load_json(path)
    _closed_object(payload, ("entries", "static_map_version"), "static_map")
    if payload["static_map_version"] != STATIC_MAP_VERSION:
        raise RestAutomationContractError("static_map.static_map_version must be the integer 1")
    entries = payload["entries"]
    if not isinstance(entries, list):
        raise RestAutomationContractError("static_map.entries must be a list")
    if not entries:
        raise RestAutomationContractError(
            "static_map.entries must contain reviewed evidence before Contract-V1 can run"
        )
    result: list[StaticMapEntry] = []
    prior: tuple[str, str, str, str] | None = None
    fields = (
        "entity", "method", "path_spec", "ref_chain", "registry", "revision",
        "route", "target_repo", "token",
    )
    for index, raw in enumerate(entries):
        context = f"static_map.entries[{index}]"
        if not isinstance(raw, dict):
            raise RestAutomationContractError(f"{context} must be an object")
        _closed_object(raw, fields, context)
        target_repo = _canonical_text(raw["target_repo"], _TOKEN, f"{context}.target_repo")
        token = _canonical_text(raw["token"], _TOKEN, f"{context}.token")
        revision = _canonical_text(raw["revision"], _VERSION, f"{context}.revision")
        method = raw["method"]
        if not isinstance(method, str) or method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise RestAutomationContractError(f"{context}.method is not supported")
        route = _canonical_path(raw["route"], "endpoint", f"{context}.route")
        path_spec = _relative_source(raw["path_spec"], f"{context}.path_spec")
        registry = raw["registry"]
        if not isinstance(registry, dict):
            raise RestAutomationContractError(f"{context}.registry must be an object")
        _closed_object(registry, ("kind", "module", "path", "release"), f"{context}.registry")
        release = _canonical_text(registry["release"], _VERSION, f"{context}.registry.release")
        module = _canonical_text(registry["module"], _TOKEN, f"{context}.registry.module")
        kind = _canonical_text(registry["kind"], _TOKEN, f"{context}.registry.kind")
        registry_path = _relative_source(registry["path"], f"{context}.registry.path")
        chain = raw["ref_chain"]
        if not isinstance(chain, list) or not all(isinstance(item, str) for item in chain):
            raise RestAutomationContractError(f"{context}.ref_chain must be a list of source paths")
        ref_chain = tuple(_relative_source(item, f"{context}.ref_chain") for item in chain)
        entity_raw = raw["entity"]
        if entity_raw is None:
            if ref_chain:
                raise RestAutomationContractError(f"{context}.ref_chain requires an entity")
            entity = evidence = None
        else:
            if not isinstance(entity_raw, dict):
                raise RestAutomationContractError(f"{context}.entity must be null or an object")
            _closed_object(entity_raw, ("evidence_file", "stem"), f"{context}.entity")
            entity = _canonical_text(entity_raw["stem"], _TOKEN, f"{context}.entity.stem")
            evidence = _relative_source(entity_raw["evidence_file"], f"{context}.entity.evidence_file")
            if ref_chain and (ref_chain[0] != path_spec or ref_chain[-1] != evidence):
                raise RestAutomationContractError(f"{context}.ref_chain must run from path_spec to evidence_file")
            if not ref_chain and evidence != path_spec:
                raise RestAutomationContractError(f"{context}.entity.evidence_file requires an explicit ref_chain")
        entry = StaticMapEntry(target_repo, token, revision, method, route, path_spec,
                               release, module, kind, registry_path, entity, evidence, ref_chain)
        map_key = (*entry.key, entry.method)
        if prior is not None and map_key <= prior:
            raise RestAutomationContractError(
                "static_map.entries must be sorted and unique by target_repo, token, revision, method"
            )
        prior = map_key
        result.append(entry)
    return result


def static_map_hashes(path: Path = STATIC_MAP_PATH) -> list[dict[str, str]]:
    """Hash the checked-in map. Source hashes are appended by the SQL audit."""
    load_static_map(path)
    raw = path.read_bytes()
    return [{"field": "static_map", "path": path.name,
             "sha1": hashlib.sha1(raw).hexdigest(), "sha256": hashlib.sha256(raw).hexdigest()}]


def audit_static_entry(
    conn: Any, entry: StaticMapEntry, *, production_repo_id: int
) -> tuple[int | None, int, list[dict[str, str]]]:
    """Fail closed against indexed source and return entity/endpoint evidence.

    The map never searches for an alternative reference chain.  The only
    acceptable edges are exactly the source-file sequence recorded in the map.
    """
    def file_row(path: str) -> Any:
        rows = conn.execute("SELECT id,path,sha1 FROM files WHERE repo_id=? AND path=?", (production_repo_id, path)).fetchall()
        if len(rows) != 1:
            raise RestAutomationContractError(f"static map source is missing or ambiguous: {path}")
        return rows[0]

    path_file = file_row(entry.path_spec)
    registry_rows = conn.execute(
        """SELECT are.registry_file_id,f.path,f.sha1 FROM api_registry_entries are
           JOIN files f ON f.id=are.registry_file_id
           WHERE are.repo_id=? AND are.registry_release=? AND are.module=?
             AND are.resource_kind=? AND are.resource_path=? AND are.revision=?""",
        (production_repo_id, entry.registry_release, entry.registry_module,
         entry.registry_kind, entry.registry_path, entry.revision),
    ).fetchall()
    if len(registry_rows) != 1:
        raise RestAutomationContractError("static map Registry selector is missing or ambiguous")
    cited = [
        {"field": "path_spec", "path": path_file["path"], "sha1": path_file["sha1"]},
        {"field": "registry", "path": registry_rows[0]["path"], "sha1": registry_rows[0]["sha1"]},
    ]
    entity_id: int | None = None
    if entry.entity is not None:
        evidence_file = file_row(entry.entity_evidence_file or "")
        indexed = conn.execute(
            "SELECT x_mapped_to FROM openapispec_index WHERE repo_id=? AND file_id=?",
            (production_repo_id, evidence_file["id"]),
        ).fetchall()
        if len(indexed) != 1:
            raise RestAutomationContractError("static map entity evidence is missing or ambiguous in openapispec_index")
        declared = indexed[0]["x_mapped_to"]
        if declared is not None and str(declared).strip() and str(declared).strip() != entry.entity:
            raise RestAutomationContractError("static map entity stem disagrees with evidence x-mappedTo")
        chain = entry.ref_chain
        for source, target in itertools.pairwise(chain):
            source_file, target_file = file_row(source), file_row(target)
            count = conn.execute(
                "SELECT COUNT(*) FROM openapi_file_ref_edges WHERE repo_id=? AND source_file_id=? AND target_file_id=?",
                (production_repo_id, source_file["id"], target_file["id"]),
            ).fetchone()[0]
            if count != 1:
                raise RestAutomationContractError(f"static map declared $ref edge is missing or ambiguous: {source} -> {target}")
        mappings = conn.execute(
            """SELECT DISTINCT em.entity_id FROM entity_mappings em
               WHERE em.repo_id=? AND em.file_id=? AND em.mapping_type LIKE 'openapispec%'""",
            (production_repo_id, evidence_file["id"]),
        ).fetchall()
        if len(mappings) != 1:
            raise RestAutomationContractError("static map entity evidence has no unique catalog mapping")
        entity_id = int(mappings[0][0])
        # entity_occurrences is identity, so prove the declared .ent stem from
        # the actual source path rather than an entity display-name convention.
        ent_rows = conn.execute(
            """SELECT f.path,f.sha1 FROM entity_occurrences eo JOIN files f ON f.id=eo.source_file_id
               WHERE eo.repo_id=? AND eo.entity_id=? AND (f.path=? OR f.path LIKE ?)""",
            (production_repo_id, entity_id, f"{entry.entity}.ent", f"%/{entry.entity}.ent"),
        ).fetchall()
        if len(ent_rows) != 1:
            raise RestAutomationContractError("static map entity stem has no unique .ent occurrence")
        cited.extend([
            {"field": "entity_evidence", "path": evidence_file["path"], "sha1": evidence_file["sha1"]},
            {"field": "entity_definition", "path": ent_rows[0]["path"], "sha1": ent_rows[0]["sha1"]},
        ])
    endpoints = conn.execute(
        """SELECT id,entity_id FROM rest_endpoints WHERE repo_id=? AND method=? AND path=?
           AND source_version=? AND file_id=?""",
        (production_repo_id, entry.method, entry.route, entry.revision, path_file["id"]),
    ).fetchall()
    if len(endpoints) != 1:
        raise RestAutomationContractError("static map endpoint materialization is missing or ambiguous")
    if entity_id is not None and endpoints[0]["entity_id"] != entity_id:
        raise RestAutomationContractError("static map endpoint entity disagrees with declared entity")
    return entity_id, int(endpoints[0]["id"]), cited
