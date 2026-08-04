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
