# parser/extractors/xslt_extractor.py

import re
from .base import Symbol

_TEMPLATE = re.compile(
    r'<xsl:template\s+[^>]*name\s*=\s*"([^"]+)"',
    re.IGNORECASE
)
_MATCH = re.compile(
    r'<xsl:template\s+[^>]*match\s*=\s*"([^"]+)"',
    re.IGNORECASE
)


def extract(source: bytes) -> list:
    text = source.decode("utf-8", errors="replace")
    symbols = []

    for m in _TEMPLATE.finditer(text):
        symbols.append(Symbol(
            name=m.group(1), kind="template", language="xslt",
            start_line=text.count("\n", 0, m.start()) + 1,
            end_line=text.count("\n", 0, m.end()) + 1,
        ))
    for m in _MATCH.finditer(text):
        symbols.append(Symbol(
            name=m.group(1), kind="template_match", language="xslt",
            start_line=text.count("\n", 0, m.start()) + 1,
            end_line=text.count("\n", 0, m.end()) + 1,
        ))

    return symbols
