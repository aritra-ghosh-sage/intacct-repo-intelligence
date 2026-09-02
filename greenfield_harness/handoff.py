"""SHA-bound, fixed-order handoff used only by the harness experiment."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import file_sha256, reference, write_json

STAGES = (
    "capture",
    "behavior_packet",
    "l1_locate",
    "l2_inspect",
    "l3_resolve",
    "analyze",
    "project",
)
OUTCOMES = frozenset({"succeeded", "degraded", "blocked", "unavailable", "skipped"})


class HarnessHandoffError(ValueError):
    pass


class HarnessHandoff:
    def __init__(self, root: Path, identity: Mapping[str, Any]) -> None:
        self.root = root.resolve()
        self.path = self.root / "harness-flow-handoff.json"
        self.body: dict[str, Any] = {
            "schema_version": "0.1",
            "artifact_kind": "greenfield_harness_flow_handoff",
            "identity": dict(identity),
            "stages": [],
            "provenance": {
                "read_only": True,
                "github_writes": "none",
                "catalog_mutation": "none",
            },
        }

    def _validate_ref(self, row: Mapping[str, Any]) -> None:
        path = self.root / str(row.get("path", ""))
        if not path.is_file() or file_sha256(path) != row.get("sha256"):
            raise HarnessHandoffError(
                "handoff artifact changed, missing, or hash-mismatched"
            )

    def complete(
        self,
        name: str,
        *,
        inputs: Mapping[str, Path],
        outputs: Mapping[str, Path],
        status: str = "succeeded",
    ) -> None:
        if name not in STAGES or status not in OUTCOMES:
            raise HarnessHandoffError("invalid harness stage or outcome")
        if any(row["name"] == name for row in self.body["stages"]):
            raise HarnessHandoffError("duplicate harness stage")
        if len(self.body["stages"]) != STAGES.index(name):
            raise HarnessHandoffError("harness stages are out of order")
        input_refs = {
            key: reference(self.root, path) for key, path in sorted(inputs.items())
        }
        output_refs = {
            key: reference(self.root, path) for key, path in sorted(outputs.items())
        }
        for row in input_refs.values():
            self._validate_ref(row)
        for row in output_refs.values():
            self._validate_ref(row)
        self.body["stages"].append(
            {
                "name": name,
                "status": status,
                "inputs": input_refs,
                "outputs": output_refs,
            }
        )

    def finish(self) -> Path:
        if tuple(row["name"] for row in self.body["stages"]) != STAGES:
            raise HarnessHandoffError("harness flow has missing mandatory stages")
        self.body["status"] = (
            "degraded"
            if any(row["status"] != "succeeded" for row in self.body["stages"])
            else "complete"
        )
        return write_json(self.path, self.body)

    @classmethod
    def validate(cls, root: Path) -> dict[str, Any]:
        body = json.loads(
            (root / "harness-flow-handoff.json").read_text(encoding="utf-8")
        )
        if (
            not isinstance(body, Mapping)
            or tuple(row.get("name") for row in body.get("stages", [])) != STAGES
        ):
            raise HarnessHandoffError("invalid or out-of-order harness handoff")
        for stage in body["stages"]:
            for direction in ("inputs", "outputs"):
                for row in stage[direction].values():
                    path = root / row["path"]
                    if not path.is_file() or file_sha256(path) != row["sha256"]:
                        raise HarnessHandoffError("handoff SHA mismatch")
        return dict(body)
