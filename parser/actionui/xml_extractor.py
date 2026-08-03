"""Safe, evidence-only extraction for legacy actionUI XML forms.

This module intentionally models the XML structure only.  It does not expand
XIncludes, evaluate event expressions, or infer a relationship from a form
name.  Later stages may resolve the extracted event calls against proven script
dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.parsers import expat

from .model import (
    ActionUiArtifact,
    Diagnostic,
    EventCallFact,
    EventFact,
    FieldFact,
    IncludeFact,
)

_XINCLUDE_NAMESPACES = {
    "http://www.w3.org/2001/XInclude",
    "http://www.w3.org/2003/XInclude",
}
_JS_IDENTIFIER_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz$_")
_JS_IDENTIFIER_PART = _JS_IDENTIFIER_START | frozenset("0123456789")
_JS_KEYWORDS = frozenset(
    {"catch", "for", "function", "if", "switch", "while", "with"}
)


@dataclass(frozen=True)
class ActionUiXmlExtractionResult:
    """Immutable XML facts. A parse failure returns only diagnostics."""

    artifacts: tuple[ActionUiArtifact, ...] = ()
    fields: tuple[FieldFact, ...] = ()
    includes: tuple[IncludeFact, ...] = ()
    events: tuple[EventFact, ...] = ()
    event_calls: tuple[EventCallFact, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass
class _Element:
    raw_name: str
    local_name: str
    start_line: int
    attributes: dict[str, str]
    text: list[tuple[str, int]] = field(default_factory=list)
    direct_path: list[tuple[str, int]] = field(default_factory=list)


class _UnsupportedDocumentType(Exception):
    pass


def _split_name(name: str) -> tuple[str | None, str]:
    if "}" in name:
        namespace, local_name = name.rsplit("}", 1)
        return namespace, local_name
    if ":" in name:
        _, local_name = name.split(":", 1)
        return None, local_name
    return None, name


def _normalized_text(parts: list[tuple[str, int]]) -> str | None:
    value = "".join(text for text, _ in parts).strip()
    return value or None


def _first_text_line(parts: list[tuple[str, int]], fallback: int) -> int:
    for text, line in parts:
        if text.strip():
            return line + text[: len(text) - len(text.lstrip())].count("\n")
    return fallback


def _event_call_names(text: str, start_line: int) -> tuple[tuple[str, int], ...]:
    """Return bare calls from event text without treating XML parsing as regex.

    This is a deliberately small JavaScript lexer. It accepts an identifier
    followed by ``(``, ignores strings/comments/member calls, and never tries to
    evaluate the event expression.
    """

    calls: list[tuple[str, int]] = []
    index = 0
    length = len(text)
    quote: str | None = None
    line = start_line

    while index < length:
        character = text[index]
        if character == "\n":
            line += 1

        if quote is not None:
            if character == "\\":
                if index + 1 < length and text[index + 1] == "\n":
                    line += 1
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue

        if character in {"'", '"', "`"}:
            quote = character
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            if newline == -1:
                break
            line += 1
            index = newline + 1
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            if close == -1:
                break
            line += text[index : close + 2].count("\n")
            index = close + 2
            continue
        if character not in _JS_IDENTIFIER_START:
            index += 1
            continue

        name_start = index
        name_line = line
        index += 1
        while index < length and text[index] in _JS_IDENTIFIER_PART:
            index += 1
        name = text[name_start:index]

        following = index
        while following < length and text[following].isspace():
            following += 1
        previous = name_start - 1
        while previous >= 0 and text[previous].isspace():
            previous -= 1
        is_member = previous >= 0 and text[previous] == "."
        if (
            name not in _JS_KEYWORDS
            and not is_member
            and following < length
            and text[following] == "("
        ):
            calls.append((name, name_line))

    return tuple(calls)


def extract_actionui_xml_facts(
    source: bytes | str, source_file: str
) -> ActionUiXmlExtractionResult:
    """Extract deterministic facts from one actionUI XML source.

    Malformed or unsupported XML produces a single diagnostic and no partial
    fact set, so callers can retain the previous successful catalog snapshot.
    """

    source_bytes = source.encode("utf-8") if isinstance(source, str) else source
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.ordered_attributes = False
    parser.specified_attributes = True
    parser.UseForeignDTD(False)
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    artifacts: list[ActionUiArtifact] = []
    fields: list[FieldFact] = []
    includes: list[IncludeFact] = []
    events: list[EventFact] = []
    event_calls: list[EventCallFact] = []
    diagnostics: list[Diagnostic] = []
    stack: list[_Element] = []

    def start_element(name: str, attrs: dict[str, str]) -> None:
        namespace, local_name = _split_name(name)
        element = _Element(
            raw_name=name,
            local_name=local_name,
            start_line=parser.CurrentLineNumber,
            attributes=dict(attrs),
        )
        stack.append(element)

        if not artifacts:
            artifacts.append(
                ActionUiArtifact(
                    source_file=source_file,
                    artifact_kind="actionui_form",
                    start_line=element.start_line,
                    end_line=element.start_line,
                    evidence=f"<{local_name}>",
                )
            )
        if local_name == "include" and namespace in _XINCLUDE_NAMESPACES:
            included_path = attrs.get("href", "").strip()
            if included_path:
                includes.append(
                    IncludeFact(
                        source_file=source_file,
                        included_path=included_path,
                        start_line=element.start_line,
                        end_line=element.start_line,
                        evidence=f"<{name} href={included_path!r}>",
                    )
                )
            else:
                diagnostics.append(
                    Diagnostic(
                        code="actionui.xml.xinclude_href_missing",
                        message="XInclude has no static href attribute.",
                        source_file=source_file,
                        start_line=element.start_line,
                        end_line=element.start_line,
                        evidence=f"<{name}>",
                    )
                )

    def character_data(data: str) -> None:
        if not stack or not data:
            return
        line = parser.CurrentLineNumber
        parent = stack[-1]
        parent.text.append((data, line))
        if parent.local_name == "path" and len(stack) >= 2 and stack[-2].local_name == "field":
            stack[-2].direct_path.append((data, line))

    def end_element(name: str) -> None:
        if not stack:
            return
        element = stack.pop()
        _, local_name = _split_name(name)
        if element.local_name != local_name:
            raise AssertionError("Expat element stack is inconsistent")

        end_line = parser.CurrentLineNumber
        parent = stack[-1] if stack else None
        if parent is not None and parent.local_name == "events":
            # Expat's buffered character callback reports the current parser
            # line, not necessarily the first line of the text chunk. Keep
            # the raw text and anchor it to the event tag for exact call lines.
            raw_event_text = "".join(text for text, _ in element.text)
            event_text = raw_event_text.strip() or None
            events.append(
                EventFact(
                    source_file=source_file,
                    event_name=element.local_name,
                    start_line=element.start_line,
                    end_line=end_line,
                    evidence=event_text or f"<{element.local_name}>",
                )
            )
            if event_text:
                for callable_name, call_line in _event_call_names(
                    raw_event_text, element.start_line
                ):
                    event_calls.append(
                        EventCallFact(
                            source_file=source_file,
                            event_name=element.local_name,
                            callable_name=callable_name,
                            start_line=call_line,
                            end_line=call_line,
                            evidence=event_text,
                        )
                    )

        if element.local_name != "field":
            return
        path = element.attributes.get("path", "").strip() or _normalized_text(element.direct_path)
        if path is None:
            path = _normalized_text(element.text)
        field_name = (
            element.attributes.get("name", "").strip()
            or element.attributes.get("id", "").strip()
            or element.attributes.get("fullname", "").strip()
            or path
        )
        if field_name is None:
            diagnostics.append(
                Diagnostic(
                    code="actionui.xml.field_identity_missing",
                    message="Field has no static name, id, fullname, or path.",
                    source_file=source_file,
                    start_line=element.start_line,
                    end_line=end_line,
                    evidence="<field>",
                )
            )
            return
        fields.append(
            FieldFact(
                source_file=source_file,
                field_name=field_name,
                field_path=path,
                start_line=element.start_line,
                end_line=end_line,
                evidence=(path or field_name),
            )
        )

    def start_doctype(*_args) -> None:
        raise _UnsupportedDocumentType("DOCTYPE declarations are not supported for actionUI XML.")

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = character_data
    parser.StartDoctypeDeclHandler = start_doctype

    try:
        parser.Parse(source_bytes, True)
    except (expat.ExpatError, _UnsupportedDocumentType, AssertionError) as error:
        line = getattr(error, "lineno", parser.CurrentLineNumber) or 1
        return ActionUiXmlExtractionResult(
            diagnostics=(
                Diagnostic(
                    code="actionui.xml.parse_error",
                    message=str(error),
                    source_file=source_file,
                    start_line=line,
                    end_line=line,
                    severity="error",
                ),
            )
        )

    return ActionUiXmlExtractionResult(
        artifacts=tuple(artifacts),
        fields=tuple(fields),
        includes=tuple(includes),
        events=tuple(events),
        event_calls=tuple(event_calls),
        diagnostics=tuple(diagnostics),
    )
