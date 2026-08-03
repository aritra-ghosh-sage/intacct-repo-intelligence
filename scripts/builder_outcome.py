"""Typed, deterministic outcomes shared by workspace refresh builders."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class BuilderDiagnostic:
    builder: str
    code: str
    severity: Literal["error", "warning"]
    source_path: str | None
    source_blob_sha: str | None
    identity: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.builder or not self.code:
            raise ValueError("diagnostic builder and code must be non-empty")
        if self.severity not in {"error", "warning"}:
            raise ValueError(f"invalid diagnostic severity: {self.severity!r}")
        if self.source_path == "<unknown>":
            raise ValueError("diagnostic source path must be null when unknown")
        normalized: dict[str, str] = {}
        for key, value in sorted(self.identity.items()):
            if not isinstance(key, str) or not key or not isinstance(value, str):
                raise TypeError(
                    "diagnostic identity must map non-empty strings to strings"
                )
            normalized[key] = value
        object.__setattr__(self, "identity", MappingProxyType(normalized))

    @property
    def diagnostic_key(self) -> str:
        return hashlib.sha256(
            _stable_json(self.to_dict(include_key=False)).encode()
        ).hexdigest()

    def to_dict(self, *, include_key: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "builder": self.builder,
            "code": self.code,
            "severity": self.severity,
            "source_path": self.source_path,
            "source_blob_sha": self.source_blob_sha,
            "identity": dict(self.identity),
        }
        if include_key:
            value["diagnostic_key"] = self.diagnostic_key
        return value


@dataclass(frozen=True)
class BuilderOutcome:
    affected_count: int | None
    metrics: Mapping[str, int]
    diagnostics: tuple[BuilderDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.affected_count is not None and (
            isinstance(self.affected_count, bool) or self.affected_count < 0
        ):
            raise ValueError("affected_count must be null or a non-negative integer")
        normalized: dict[str, int] = {}
        for key, value in sorted(self.metrics.items()):
            if not isinstance(key, str) or not key:
                raise TypeError("metric names must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"metric {key!r} must be a non-negative integer")
            normalized[key] = value
        diagnostics = tuple(
            sorted(self.diagnostics, key=lambda item: item.diagnostic_key)
        )
        if len({item.diagnostic_key for item in diagnostics}) != len(diagnostics):
            raise ValueError("duplicate builder diagnostic")
        object.__setattr__(self, "metrics", MappingProxyType(normalized))
        object.__setattr__(self, "diagnostics", diagnostics)

    def to_dict(self) -> dict[str, object]:
        return {
            "affected_count": self.affected_count,
            "metrics": dict(self.metrics),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def to_json(self) -> str:
        return _stable_json(self.to_dict())
