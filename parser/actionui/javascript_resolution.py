"""Surface-scoped JavaScript handler resolution for actionUI event calls."""

from __future__ import annotations

import re

from .model import (
    EventCallFact,
    HandlerResolutionFact,
    JavascriptExtractionResult,
    JavascriptSymbolFact,
    ScriptDependencyFact,
)

_EXACT_CALLABLE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_JAVASCRIPT_PARSE_ERROR = "actionui.javascript.parse_error"


def _scope(dependency: ScriptDependencyFact) -> str:
    return (
        dependency.activation_state
        if dependency.activation_state in {"active", "conditional"}
        else "unresolved"
    )


def _has_parse_diagnostic(result: JavascriptExtractionResult) -> bool:
    """A malformed linked file cannot provide handler-resolution evidence."""

    return any(diagnostic.code == _JAVASCRIPT_PARSE_ERROR for diagnostic in result.diagnostics)


def resolve_event_handlers(
    event_calls: tuple[EventCallFact, ...] | list[EventCallFact],
    dependencies: tuple[ScriptDependencyFact, ...] | list[ScriptDependencyFact],
    javascript_results: tuple[JavascriptExtractionResult, ...] | list[JavascriptExtractionResult],
) -> tuple[HandlerResolutionFact, ...]:
    """Resolve calls solely against the supplied dependencies for one surface.

    The caller is responsible for providing dependencies from exactly one UI
    surface.  No global JavaScript index is consulted, which prevents a
    same-named function in an unrelated script from becoming false evidence.
    """

    symbols_by_file: dict[str, tuple[JavascriptSymbolFact, ...]] = {}
    parse_error_files: set[str] = set()
    for result in javascript_results:
        if _has_parse_diagnostic(result):
            # Do not let a valid declaration elsewhere in this malformed file
            # prove that an XML event handler can execute.
            parse_error_files.add(result.source_file)
            symbols_by_file.pop(result.source_file, None)
            continue
        if result.source_file not in parse_error_files:
            symbols_by_file[result.source_file] = result.symbols
    linked: list[tuple[ScriptDependencyFact, JavascriptSymbolFact]] = []
    for dependency in dependencies:
        for symbol in symbols_by_file.get(dependency.script_path, ()):
            linked.append((dependency, symbol))

    outcomes: list[HandlerResolutionFact] = []
    for event_call in event_calls:
        name = event_call.callable_name
        if not _EXACT_CALLABLE.fullmatch(name):
            outcomes.append(
                HandlerResolutionFact(
                    event_call=event_call,
                    dependency=None,
                    handler_symbol=None,
                    resolution_status="unsupported",
                    resolution_reason="member_or_dynamic_handler_expression",
                )
            )
            continue

        candidates = [
            pair for pair in linked
            if pair[1].symbol_name == name and pair[1].parent_symbol is None
        ]
        blocked_candidates = [
            dependency
            for dependency in dependencies
            if dependency.script_path in parse_error_files
        ]
        active = [pair for pair in candidates if _scope(pair[0]) == "active"]
        conditional = [pair for pair in candidates if _scope(pair[0]) == "conditional"]
        if len(active) == 1 and not conditional:
            dependency, symbol = active[0]
            status = "resolved"
            reason = "unique_active_exact_callable"
        elif active and (conditional or len(active) > 1):
            dependency = None
            symbol = None
            status = "ambiguous"
            reason = "mixed_active_and_conditional" if conditional else "multiple_active_exact_callables"
        elif conditional:
            dependency = conditional[0][0] if len(conditional) == 1 else None
            symbol = conditional[0][1] if len(conditional) == 1 else None
            status = "conditional"
            reason = "conditional_exact_callable"
        else:
            dependency = None
            symbol = None
            status = "unresolved"
            reason = (
                "linked_script_parse_error"
                if blocked_candidates
                else "no_linked_exact_callable"
            )
        outcomes.append(
            HandlerResolutionFact(
                event_call=event_call,
                dependency=dependency,
                handler_symbol=symbol,
                resolution_status=status,
                resolution_reason=reason,
            )
        )
    return tuple(outcomes)
