"""Immutable, wrapper-owned input/output handoffs for Greenfield flows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.artifact_io import write_json_atomic

SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_flow_handoff"
STAGE_OUTCOMES = frozenset(
    {"succeeded", "degraded", "blocked", "unavailable", "skipped", "failed"}
)
_STAGE_GROUPS = (
    ("step1",),
    ("request",),
    ("capture",),
    ("step1_5",),
    ("repository_context",),
    ("impact_discovery",),
    ("inventory",),
    ("step2",),
    ("step3",),
    ("step4",),
    ("step5",),
    ("strands_planning",),
    ("analyze",),
    ("behavior_impact_report",),
    ("test_assessment",),
    ("test_proposal",),
    ("pr_review",),
    ("step6", "step6_handoff"),
    ("step7", "step7_handoff"),
    ("step8_preparation", "step8_handoff"),
    ("publish",),
)
_STAGE_INDEX = {
    name: index for index, group in enumerate(_STAGE_GROUPS) for name in group
}


class FlowHandoffError(ValueError):
    """Raised when a wrapper stage cannot be bound to retained evidence."""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Compare immutable source identity while accepting retained field aliases."""

    return {
        "repository": value.get("repository"),
        "repo_key": value.get("repo_key"),
        "pr_number": value.get("pr_number"),
        "base_revision": value.get("base_revision", value.get("base_sha")),
        "head_revision": value.get(
            "head_revision", value.get("head_sha", value.get("target_revision"))
        ),
        "changed_paths": value.get("changed_paths"),
    }


def validate_legacy_handoff(
    output_dir: str | Path, *, source: Mapping[str, Any]
) -> list[str]:
    """Validate a retained handoff for inspection without permitting resume."""

    root = Path(output_dir).resolve()
    path = root / "flow.handoff.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping) or not isinstance(value.get("source"), Mapping):
            raise FlowHandoffError("existing bundle source identity does not match")
        if _source_identity(value["source"]) != _source_identity(source):
            raise FlowHandoffError("existing bundle source identity does not match")
        validator = object.__new__(GreenfieldFlowHandoff)
        validator.output_dir = root
        validator.path = path
        validator._validate_body(value, allow_legacy_replay=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return [str(exc)]
    return []


class GreenfieldFlowHandoff:
    """Record wrapper stage outcomes with exact, revalidated file handoffs."""

    def __init__(
        self,
        output_dir: str | Path,
        *,
        source: Mapping[str, Any],
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.path = self.output_dir / "flow.handoff.json"
        if self.path.exists():
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(existing, dict)
                or not isinstance(existing.get("source"), Mapping)
                or _source_identity(existing["source"]) != _source_identity(source)
            ):
                raise FlowHandoffError("existing bundle source identity does not match")
            self._validate_body(existing)
            if existing.get("status") != "running":
                raise FlowHandoffError("immutable Greenfield bundle is already complete or terminal")
            self._body = existing
            return
        self._body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "analysis_kind": ANALYSIS_KIND,
            "status": "running",
            "source": dict(source),
            "stages": [],
            "provenance": {
                "read_only": True,
                "catalog_mutation": "none",
                "github_writes": "none",
            },
        }
        self._write()

    def _reference(self, path: str | Path) -> dict[str, str]:
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FlowHandoffError(f"handoff artifact is missing: {resolved}")
        try:
            label = str(resolved.relative_to(self.output_dir))
        except ValueError:
            label = str(resolved)
        return {"path": label, "sha256": _file_sha256(resolved)}

    def _validate_reference(self, reference: Any, label: str) -> None:
        if not isinstance(reference, Mapping):
            raise FlowHandoffError(f"{label} must be an artifact reference")
        path_value = reference.get("path")
        digest = reference.get("sha256")
        if not isinstance(path_value, str) or not path_value:
            raise FlowHandoffError(f"{label}.path is required")
        if not isinstance(digest, str) or len(digest) != 64:
            raise FlowHandoffError(f"{label}.sha256 is invalid")
        path = Path(path_value)
        resolved = path if path.is_absolute() else self.output_dir / path
        if not resolved.is_file():
            raise FlowHandoffError(f"handoff artifact is missing: {resolved.resolve()}")
        if _file_sha256(resolved.resolve()) != digest:
            raise FlowHandoffError(f"handoff artifact changed after capture: {resolved.resolve()}")

    def _validate_body(
        self, body: Mapping[str, Any], *, allow_legacy_replay: bool = False
    ) -> None:
        if body.get("schema_version") != SCHEMA_VERSION:
            raise FlowHandoffError("unsupported flow handoff schema")
        if body.get("analysis_kind") != ANALYSIS_KIND:
            raise FlowHandoffError("invalid flow handoff analysis kind")
        stages = body.get("stages")
        if not isinstance(stages, list):
            raise FlowHandoffError("flow handoff stages must be a list")
        previous_index = -1
        seen: set[str] = set()
        outputs: dict[str, str] = {}
        for stage_index, stage in enumerate(stages):
            if not isinstance(stage, Mapping):
                raise FlowHandoffError(f"stage {stage_index} must be an object")
            name = stage.get("name")
            if not isinstance(name, str) or name not in _STAGE_INDEX:
                raise FlowHandoffError(f"invalid flow stage: {name}")
            if name in seen:
                raise FlowHandoffError(f"duplicate flow stage: {name}")
            seen.add(name)
            index = _STAGE_INDEX[name]
            if index <= previous_index:
                raise FlowHandoffError(f"flow stages are out of order: {name}")
            previous_index = index
            outcome = stage.get("status")
            if outcome == "complete":
                outcome = "succeeded"
            if outcome not in STAGE_OUTCOMES:
                raise FlowHandoffError(f"invalid outcome for flow stage: {name}")
            for direction in ("inputs", "outputs"):
                references = stage.get(direction)
                if not isinstance(references, Mapping):
                    raise FlowHandoffError(f"{name}.{direction} must be an object")
                if (
                    outcome in {"succeeded", "degraded"}
                    and direction == "outputs"
                    and not references
                    and not allow_legacy_replay
                ):
                    raise FlowHandoffError(f"{outcome} flow stage output is missing: {name}")
                for key, reference in references.items():
                    label = f"{name}.{direction}.{key}"
                    self._validate_reference(reference, label)
                    if direction == "outputs":
                        outputs[str(reference["path"])] = str(reference["sha256"])
                    elif str(reference["path"]) in outputs and outputs[str(reference["path"])] != reference["sha256"]:
                        raise FlowHandoffError(f"{label} does not match its producing output")

    def _validate_progress(self, *, require_complete: bool = False) -> None:
        self._validate_body(self._body)
        if not require_complete:
            return
        stages = self._body["stages"]
        if len(stages) != len(_STAGE_GROUPS):
            raise FlowHandoffError("flow is missing mandatory stages")
        for index, group in enumerate(_STAGE_GROUPS):
            if not any(stage.get("name") in group for stage in stages):
                raise FlowHandoffError(f"flow is missing mandatory stage group {index}")

    def _references(self, paths: Mapping[str, str | Path]) -> dict[str, dict[str, str]]:
        return {
            name: self._reference(path)
            for name, path in sorted(paths.items())
        }

    def complete_stage(
        self,
        name: str,
        *,
        inputs: Mapping[str, str | Path],
        outputs: Mapping[str, str | Path],
        status: str = "succeeded",
    ) -> None:
        if status not in STAGE_OUTCOMES:
            raise FlowHandoffError(f"invalid flow stage outcome: {status}")
        if not name or name not in _STAGE_INDEX:
            raise FlowHandoffError(f"invalid flow stage: {name}")
        if any(row["name"] == name for row in self._body["stages"]):
            raise FlowHandoffError(f"invalid or duplicate flow stage: {name}")
        current_index = _STAGE_INDEX[name]
        previous_indices = [_STAGE_INDEX[row["name"]] for row in self._body["stages"]]
        if previous_indices and current_index != max(previous_indices) + 1:
            raise FlowHandoffError(f"flow stage is out of order: {name}")
        if status in {"succeeded", "degraded"} and not outputs:
            raise FlowHandoffError(f"{status} flow stage output is missing: {name}")
        input_refs = self._references(inputs)
        output_refs = self._references(outputs)
        known_outputs = {
            str(reference["path"]): str(reference["sha256"])
            for stage in self._body["stages"]
            for reference in stage.get("outputs", {}).values()
        }
        for key, reference in input_refs.items():
            expected = known_outputs.get(str(reference["path"]))
            if expected is not None and expected != reference["sha256"]:
                raise FlowHandoffError(f"stage input changed after producer completed: {key}")
        self._body["stages"].append(
            {
                "name": name,
                "status": status,
                "inputs": input_refs,
                "outputs": output_refs,
            }
        )
        self._write()

    def fail(
        self,
        stage: str,
        error: BaseException,
        *,
        contract_path: str | Path | None = None,
        diagnostics: Mapping[str, str | Path] | None = None,
    ) -> None:
        self._body["status"] = "failed"
        failure: dict[str, Any] = {"stage": stage, "reason": str(error)}
        if contract_path is not None:
            failure["contract_path"] = str(contract_path)
        if diagnostics:
            failure["diagnostics"] = self._references(diagnostics)
        self._body["failure"] = failure
        self._write()

    def finish(self) -> dict[str, Any]:
        self._validate_progress(require_complete=True)
        outcomes = [stage["status"] for stage in self._body["stages"]]
        if "failed" in outcomes:
            self._body["status"] = "failed"
        elif "blocked" in outcomes:
            self._body["status"] = "blocked"
        elif any(outcome in {"degraded", "unavailable", "skipped"} for outcome in outcomes):
            self._body["status"] = "degraded"
        else:
            self._body["status"] = "complete"
        self._write()
        return dict(self._body)

    def _write(self) -> None:
        write_json_atomic(self.path, self._body)
