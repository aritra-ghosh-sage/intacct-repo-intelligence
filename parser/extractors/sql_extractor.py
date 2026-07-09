# parser/extractors/sql_extractor.py

import re
from .base import Symbol

_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?([`\"\[]?\w+[`\"\]]?)", re.IGNORECASE
)
_CREATE_VIEW = re.compile(
    r"create\s+(?:or\s+replace\s+)?view\s+([`\"\[]?\w+[`\"\]]?)", re.IGNORECASE
)
_CREATE_PROC = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:procedure|function)\s+([`\"\[]?\w+[`\"\]]?)",
    re.IGNORECASE,
)


def _clean(name: str) -> str:
    return name.strip('`"[]')


def extract(source: bytes) -> list:
    text = source.decode("utf-8", errors="replace")
    symbols = []

    for m in _CREATE_TABLE.finditer(text):
        symbols.append(
            Symbol(
                name=_clean(m.group(1)),
                kind="table",
                language="sql",
                start_line=text.count("\n", 0, m.start()) + 1,
                end_line=text.count("\n", 0, m.end()) + 1,
            )
        )
    for m in _CREATE_VIEW.finditer(text):
        symbols.append(
            Symbol(
                name=_clean(m.group(1)),
                kind="view",
                language="sql",
                start_line=text.count("\n", 0, m.start()) + 1,
                end_line=text.count("\n", 0, m.end()) + 1,
            )
        )
    for m in _CREATE_PROC.finditer(text):
        symbols.append(
            Symbol(
                name=_clean(m.group(1)),
                kind="procedure",
                language="sql",
                start_line=text.count("\n", 0, m.start()) + 1,
                end_line=text.count("\n", 0, m.end()) + 1,
            )
        )

    return symbols
