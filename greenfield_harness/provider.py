"""Future investigator boundary; no provider is invoked by this experiment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class InvestigatorProvider(Protocol):
    """May propose work from a compact case summary, never source blobs."""

    def propose(
        self, case_summary: Mapping[str, Any]
    ) -> Sequence[Mapping[str, Any]]: ...


class FakeInvestigatorProvider:
    """Deterministic test double; proposals require ledger validation before use."""

    def __init__(self, proposals: Sequence[Mapping[str, Any]] = ()) -> None:
        self.proposals = [dict(row) for row in proposals]
        self.calls: list[dict[str, Any]] = []

    def propose(self, case_summary: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        self.calls.append(dict(case_summary))
        return list(self.proposals)
