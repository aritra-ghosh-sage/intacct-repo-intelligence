# parser/extractors/base.py

from dataclasses import dataclass
from typing import Optional


@dataclass
class Symbol:
    name: str
    kind: str
    language: str
    start_line: int
    end_line: int
    parent_symbol: Optional[str] = None
    signature: Optional[str] = None
