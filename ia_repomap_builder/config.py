"""Stable input/output contracts for the repository-map comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# These are the PHP-family extensions observed in ia-app. ``.map`` is a
# scoped alias: only files under app/source may use it because third-party
# JavaScript/CSS source maps also exist under app/resources.
PHP_FAMILY_EXTENSIONS = frozenset(
    {
        ".php",
        ".phtml",
        ".cls",
        ".ent",
        ".inc",
        ".cqry",
        ".rpt",
        ".menu",
        ".pol",
        ".wfl",
        ".shortcuts",
        ".qry",
        ".bin",
        ".map",
    }
)


@dataclass(frozen=True)
class BuildRequest:
    """A reproducible context request.

    ``scope`` is root-relative.  A query is optional so an engine can produce
    a repository overview; the first release is optimized for task-shaped
    queries.  ``engine`` is one of ``lexical``, ``aider`` or ``ripwire``.
    """

    repo_root: Path
    scope: tuple[str, ...] = ("app/source",)
    query: str | None = None
    token_budget: int = 4000
    engine: str = "lexical"
    revision: str | None = None


@dataclass(frozen=True)
class ContextItem:
    path: str
    symbol: str | None = None
    kind: str | None = None
    line: int | None = None
    score: float | None = None
    evidence: str | None = None


@dataclass
class BuildResult:
    """Common output shape for all engines.

    ``status`` is ``ok`` or ``unavailable``/``error``.  An unavailable
    optional engine is a valid result and carries an actionable diagnostic.
    """

    engine: str
    status: str
    items: list[ContextItem] = field(default_factory=list)
    context: str = ""
    diagnostics: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "status": self.status,
            "items": [item.__dict__ for item in self.items],
            "context": self.context,
            "diagnostics": list(self.diagnostics),
            "metrics": dict(self.metrics),
            "identity": dict(self.identity),
        }
