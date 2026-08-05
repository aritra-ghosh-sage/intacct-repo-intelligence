"""ActionUI facts derived from the generic JavaScript callable extractor."""

from __future__ import annotations

from parser.actionui.model import (
    Diagnostic,
    JavascriptExtractionResult,
    JavascriptSymbolFact,
)
from parser.extractors import javascript_extractor
from parser.extractors.base import Symbol


def extract_javascript_callables(
    source: bytes, source_file: str
) -> JavascriptExtractionResult:
    """Return source-provenanced callables and parser diagnostics for one script."""

    failures = javascript_extractor.get_parse_failures()
    before = len(failures)
    symbols = javascript_extractor.extract(source, source_file)
    diagnostics = tuple(
        Diagnostic(
            code="actionui.javascript.parse_error",
            message="JavaScript parser error intersects unsupported source.",
            source_file=source_file,
            start_line=int(failure["start_line"]),
            end_line=int(failure["end_line"]),
            # Parser coverage loss is source evidence, not a catalog-integrity
            # failure.  The unresolved handler remains recorded separately.
            severity="warning",
            evidence=(
                f"{failure['node_type']} bytes {failure['start_byte']}-{failure['end_byte']}"
            ),
        )
        for failure in javascript_extractor.get_parse_failures()[before:]
    )
    return JavascriptExtractionResult(
        source_file=source_file,
        symbols=tuple(
            JavascriptSymbolFact(
                source_file=source_file,
                symbol_name=symbol.name,
                symbol_kind=symbol.kind,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                evidence=_text_for_symbol(symbol, source),
                parent_symbol=symbol.parent_symbol,
            )
            for symbol in symbols
        ),
        diagnostics=diagnostics,
    )


def _text_for_symbol(symbol: Symbol, source: bytes) -> str:
    """Retain a compact, deterministic declaration line as source evidence."""

    lines = source.decode("utf-8", errors="replace").splitlines()
    if 1 <= symbol.start_line <= len(lines):
        return lines[symbol.start_line - 1].strip()
    return symbol.name
