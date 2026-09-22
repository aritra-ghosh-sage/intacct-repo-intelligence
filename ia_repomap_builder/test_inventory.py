"""Deterministic discovery and persistence for the external test repository."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "ia-repomap.test-inventory/v1"
MANIFEST_SCHEMA = "ia-repomap.test-inventory-manifest/v1"
SCANNER_VERSION = "v1"
UTC = getattr(__import__("datetime"), "UTC", timezone.__dict__["utc"])

_FEATURE_RE = re.compile(r"^\s*Feature:\s*(.+?)\s*$", re.IGNORECASE)
_SCENARIO_RE = re.compile(r"^\s*(?:Scenario|Scenario Outline):\s*(.+?)\s*$", re.IGNORECASE)
_TAG_RE = re.compile(r"^\s*((?:@[^\s]+\s*)+)$")
_REQUEST_RE = re.compile(r'file\s+"([^"]+\.json)"', re.IGNORECASE)
_METHOD_RE = re.compile(r'"(GET|POST|PUT|PATCH|DELETE)"', re.IGNORECASE)


@dataclass(frozen=True)
class InventoryGap:
    kind: str
    path: str | None
    detail: str


@dataclass(frozen=True)
class InventoryScenario:
    name: str
    tags: tuple[str, ...] = ()
    fixtures: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class InventorySuite:
    suite_id: str
    module: str | None
    category: str
    feature_files: tuple[str, ...] = ()
    input_files: tuple[str, ...] = ()
    output_files: tuple[str, ...] = ()
    scenarios: tuple[InventoryScenario, ...] = ()
    tags: tuple[str, ...] = ()
    api_objects: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class InventoryRepository:
    root: str
    repository_id: str
    head: str | None
    dirty: bool | None
    scanner_version: str
    inventory_digest: str


@dataclass(frozen=True)
class TestInventory:
    schema: str
    status: str
    repository: InventoryRepository
    suites: tuple[InventorySuite, ...] = ()
    gaps: tuple[InventoryGap, ...] = ()
    metrics: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "repository": asdict(self.repository),
            "suites": [_suite_dict(suite) for suite in self.suites],
            "gaps": [asdict(gap) for gap in self.gaps],
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class PersistedInventory:
    inventory: TestInventory
    inventory_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def _git_output(root: Path, arguments: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _repository_id(root: Path) -> str:
    remote = _git_output(root, ["remote", "get-url", "origin"])
    seed = f"origin={remote}" if remote else f"name={root.name}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def _dirty(root: Path) -> bool | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(completed.stdout.strip())


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _suite_id(path: str) -> tuple[str, str | None]:
    parts = Path(path).parts
    try:
        features_index = parts.index("features")
        module = parts[features_index + 1]
    except (ValueError, IndexError):
        return path, None
    suffix = list(parts[features_index + 2 :])
    for marker in ("input", "output"):
        if marker in suffix:
            marker_index = suffix.index(marker)
            if marker_index + 1 < len(suffix):
                return "/".join((*parts[: features_index + 2], "input", suffix[marker_index + 1])), module
    return "/".join(parts[: features_index + 2]), module


def _parse_feature(path: str, content: str) -> tuple[str | None, tuple[InventoryScenario, ...], tuple[str, ...]]:
    feature_name = None
    pending_tags: set[str] = set()
    scenario_name: str | None = None
    scenario_tags: set[str] = set()
    fixtures: set[str] = set()
    methods: set[str] = set()
    scenarios: list[InventoryScenario] = []

    def finish_scenario() -> None:
        if scenario_name is not None:
            scenarios.append(InventoryScenario(
                name=scenario_name,
                tags=tuple(sorted(scenario_tags)),
                fixtures=tuple(sorted(fixtures)),
                methods=tuple(sorted(methods)),
            ))

    for line in content.splitlines():
        if feature_name is None:
            match = _FEATURE_RE.match(line)
            if match:
                feature_name = match.group(1)
        tag_match = _TAG_RE.match(line)
        if tag_match:
            pending_tags.update(tag_match.group(1).split())
        scenario_match = _SCENARIO_RE.match(line)
        if scenario_match:
            finish_scenario()
            scenario_name = scenario_match.group(1)
            scenario_tags = pending_tags
            pending_tags = set()
            fixtures = set()
            methods = set()
            continue
        if scenario_name is not None:
            fixtures.update(_REQUEST_RE.findall(line))
            methods.update(item.upper() for item in _METHOD_RE.findall(line))
    finish_scenario()
    if not scenarios:
        scenarios.append(InventoryScenario(
            name=feature_name or Path(path).stem,
            tags=tuple(sorted(pending_tags)),
        ))
    return feature_name, tuple(scenarios), tuple(sorted(pending_tags))


def _read_json_metadata(root: Path, path: Path, gaps: list[InventoryGap]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    relative = _relative(root, path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        gaps.append(InventoryGap("file_unreadable", relative, str(exc)))
        return (), ()
    objects: set[str] = set()
    if isinstance(payload, dict):
        values = [payload]
        for value in payload.values():
            if isinstance(value, dict):
                values.append(value)
        for value in values:
            object_name = value.get("object")
            if isinstance(object_name, str) and object_name:
                objects.add(object_name)
    return tuple(sorted(objects)), ()


def _suite_dict(suite: InventorySuite) -> dict[str, Any]:
    value = asdict(suite)
    value["scenarios"] = [asdict(item) for item in suite.scenarios]
    return value


def _canonical_suites(suites: tuple[InventorySuite, ...]) -> bytes:
    return json.dumps(
        [_suite_dict(suite) for suite in suites],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_test_inventory(test_repo_root: Path) -> TestInventory:
    """Discover test suites under ``features`` without executing tests."""

    root = test_repo_root.expanduser().resolve(strict=True)
    features = root / "features"
    gaps: list[InventoryGap] = []
    grouped: dict[str, dict[str, Any]] = {}
    if not features.is_dir():
        gaps.append(InventoryGap("features_directory_missing", "features", "features directory is missing"))
    else:
        for path in sorted(item for item in features.rglob("*") if item.is_file()):
            relative = _relative(root, path)
            suite_id, module = _suite_id(relative)
            record = grouped.setdefault(
                suite_id,
                {"module": module, "feature_files": [], "input_files": [], "output_files": [], "scenarios": [], "tags": set(), "api_objects": set(), "methods": set()},
            )
            if path.suffix.lower() == ".feature":
                record["feature_files"].append(relative)
                try:
                    _, scenarios, tags = _parse_feature(relative, path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as exc:
                    gaps.append(InventoryGap("file_unreadable", relative, str(exc)))
                    continue
                record["scenarios"].extend(scenarios)
                record["tags"].update(tags)
                record["tags"].update(tag for scenario in scenarios for tag in scenario.tags)
                record["methods"].update(method for scenario in scenarios for method in scenario.methods)
            elif "input" in path.parts:
                record["input_files"].append(relative)
                objects, _ = _read_json_metadata(root, path, gaps) if path.suffix.lower() == ".json" else ((), ())
                record["api_objects"].update(objects)
            elif "output" in path.parts:
                record["output_files"].append(relative)

    suites: list[InventorySuite] = []
    for suite_id in sorted(grouped):
        record = grouped[suite_id]
        has_feature = bool(record["feature_files"])
        has_fixture = bool(record["input_files"] or record["output_files"])
        category = "executable_and_fixture" if has_feature and has_fixture else "executable" if has_feature else "fixture" if has_fixture else "unclassified"
        suites.append(InventorySuite(
            suite_id=suite_id,
            module=record["module"],
            category=category,
            feature_files=tuple(sorted(record["feature_files"])),
            input_files=tuple(sorted(record["input_files"])),
            output_files=tuple(sorted(record["output_files"])),
            scenarios=tuple(sorted(record["scenarios"], key=lambda item: (item.name, item.tags))),
            tags=tuple(sorted(record["tags"])),
            api_objects=tuple(sorted(record["api_objects"])),
            methods=tuple(sorted(record["methods"])),
        ))

    suites_tuple = tuple(suites)
    canonical = _canonical_suites(suites_tuple)
    digest = hashlib.sha256(canonical).hexdigest()
    head = _git_output(root, ["rev-parse", "--verify", "HEAD^{commit}"])
    dirty = _dirty(root)
    status = "ok" if head is not None and dirty is not None else "unavailable"
    metrics = {
        "indexed_files": sum(len(item.feature_files) + len(item.input_files) + len(item.output_files) for item in suites_tuple),
        "suite_count": len(suites_tuple),
        "feature_files": sum(len(item.feature_files) for item in suites_tuple),
        "fixture_files": sum(len(item.input_files) + len(item.output_files) for item in suites_tuple),
        "diagnostics": len(gaps),
    }
    repository = InventoryRepository(str(root), _repository_id(root), head, dirty, SCANNER_VERSION, digest)
    return TestInventory(SCHEMA, status, repository, suites_tuple, tuple(sorted(gaps, key=lambda item: (item.path or "", item.kind, item.detail))), metrics)


def persist_test_inventory(inventory: TestInventory, artifact_root: Path) -> PersistedInventory:
    """Atomically persist an inventory under an external artifact root."""

    root = artifact_root.expanduser().resolve(strict=False)
    if not root.is_absolute():
        raise ValueError("artifact_root must be absolute")
    repository_root = Path(inventory.repository.root).resolve(strict=False)
    if root == repository_root or repository_root in root.parents:
        raise ValueError("artifact_root must be outside the test repository")
    if inventory.repository.dirty is None or not inventory.repository.head:
        raise ValueError("cannot persist inventory without Git revision identity")
    if inventory.repository.dirty:
        raise ValueError("cannot persist inventory from a dirty test repository")
    destination = root / "test-inventory" / inventory.repository.repository_id / inventory.repository.head / inventory.repository.inventory_digest
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"inventory destination is not empty: {destination}")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=parent))
    inventory_bytes = (json.dumps(inventory.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "repository_id": inventory.repository.repository_id,
        "head": inventory.repository.head,
        "dirty": inventory.repository.dirty,
        "scanner_version": inventory.repository.scanner_version,
        "inventory_digest": inventory.repository.inventory_digest,
        "inventory_relative_path": "inventory.json",
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "inventory_bytes": len(inventory_bytes),
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    try:
        inventory_path = staging / "inventory.json"
        manifest_path = staging / "manifest.json"
        inventory_path.write_bytes(inventory_bytes)
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        for path in (inventory_path, manifest_path):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return PersistedInventory(inventory, destination / "inventory.json", destination / "manifest.json", manifest)
