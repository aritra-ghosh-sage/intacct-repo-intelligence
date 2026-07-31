# parser/extractors/base.py

from dataclasses import dataclass


@dataclass
class Symbol:
    name: str
    kind: str
    language: str
    start_line: int
    end_line: int
    parent_symbol: str | None = None
    signature: str | None = None
