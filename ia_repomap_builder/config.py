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

REPOMAP_CONFIG_FILENAME = ".ia-repomap.toml"
REPOMAP_SCHEMA_VERSION = 1


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
    engine: str = "ripwire"
    revision: str | None = None


@dataclass(frozen=True)
class ContextItem:
    """One ranked item returned by a general repository-map engine."""

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


@dataclass(frozen=True)
class RepoMapConfig:
    """Validated repository-local configuration for the Ripwire PR slice."""

    schema_version: int
    engine: str
    scope: tuple[str, ...]
    token_budget: int
    php_family_extensions: tuple[str, ...]
    map_php_scope: tuple[str, ...]


@dataclass(frozen=True)
class PrepareRepoMapRequest:
    """Prepare a revision-bound Ripwire index outside a repository checkout."""

    repo_root: Path
    artifact_root: Path


@dataclass(frozen=True)
class PrContextRequest:
    """Request deterministic Ripwire PR-context seed evidence.

    The checked-out clean ``HEAD`` is the PR head. ``base_ref`` is an explicit
    local Git reference; this request deliberately accepts no GitHub PR number
    and performs no remote PR lookup.
    """

    repo_root: Path
    artifact_root: Path
    base_ref: str
    token_budget: int | None = None
    limit: int = 20
    offset: int = 0
    history_commits: int = 500


@dataclass(frozen=True)
class PrCallerCandidate:
    """A candidate direct caller of a changed symbol."""

    path: str
    name: str
    line: int
    kind: str | None = None
    confidence: str = "candidate"


@dataclass(frozen=True)
class PrSymbolCandidate:
    """A candidate symbol attributed to a changed file or hunk."""

    path: str
    name: str
    line: int | None
    kind: str | None = None
    confidence: str = "candidate"
    callers: tuple[PrCallerCandidate, ...] = ()


@dataclass(frozen=True)
class PrChangedFile:
    """A Git-authoritative changed path with optional candidate symbols."""

    path: str
    change: str
    old_path: str | None = None
    symbols: tuple[PrSymbolCandidate, ...] = ()


@dataclass(frozen=True)
class PrContextGap:
    """An explicit limitation, omission, or uncertainty in PR evidence."""

    kind: str
    detail: str
    count: int | None = None


@dataclass
class PrContextResult:
    """PR-context evidence, including verbatim Ripwire XML and explicit gaps."""

    status: str
    changed_files: list[PrChangedFile] = field(default_factory=list)
    raw_xml: str = ""
    gaps: list[PrContextGap] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "changed_files": [
                {
                    "path": changed.path,
                    "change": changed.change,
                    "old_path": changed.old_path,
                    "symbols": [
                        {
                            "path": symbol.path,
                            "name": symbol.name,
                            "line": symbol.line,
                            "kind": symbol.kind,
                            "confidence": symbol.confidence,
                            "callers": [caller.__dict__ for caller in symbol.callers],
                        }
                        for symbol in changed.symbols
                    ],
                }
                for changed in self.changed_files
            ],
            "raw_xml": self.raw_xml,
            "gaps": [gap.__dict__ for gap in self.gaps],
            "diagnostics": list(self.diagnostics),
            "metrics": dict(self.metrics),
            "identity": dict(self.identity),
        }
