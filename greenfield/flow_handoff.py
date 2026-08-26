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


class FlowHandoffError(ValueError):
    """Raised when a wrapper stage cannot be bound to retained evidence."""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GreenfieldFlowHandoff:
    """Record each completed wrapper stage with its exact file handoffs."""

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
            if not isinstance(existing, dict) or existing.get("source") != dict(source):
                raise FlowHandoffError("existing bundle source identity does not match")
            if existing.get("status") == "complete":
                raise FlowHandoffError("immutable Greenfield bundle is already complete")
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
    ) -> None:
        if not name or any(row["name"] == name for row in self._body["stages"]):
            raise FlowHandoffError(f"invalid or duplicate flow stage: {name}")
        self._body["stages"].append(
            {
                "name": name,
                "status": "complete",
                "inputs": self._references(inputs),
                "outputs": self._references(outputs),
            }
        )
        self._write()

    def fail(self, stage: str, error: BaseException) -> None:
        self._body["status"] = "failed"
        self._body["failure"] = {"stage": stage, "reason": str(error)}
        self._write()

    def finish(self) -> dict[str, Any]:
        self._body["status"] = "complete"
        self._write()
        return dict(self._body)

    def _write(self) -> None:
        write_json_atomic(self.path, self._body)
