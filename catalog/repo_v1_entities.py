"""Snapshot-only extraction of minimal repository-local ``.ent`` occurrences."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from catalog.source_snapshot import SourceSnapshot, SourceSnapshotError

ENTITY_EXTRACTOR = "repo_v1_entities_v1"
ENTITY_DIAGNOSTIC_CODES = frozenset(
    {
        "entity_source_read_error",
        "entity_identity_missing",
        "entity_identity_invalid",
        "entity_metadata_missing",
        "entity_metadata_dynamic",
        "entity_metadata_conflict",
        "entity_include_missing",
        "entity_include_unresolved",
        "entity_include_dynamic",
        "entity_include_ambiguous",
        "entity_include_cycle",
        "entity_reference_missing",
        "entity_reference_dynamic",
        "entity_reference_ambiguous",
        "entity_reference_cycle",
    }
)
_METADATA_FIELDS = ("module", "table", "view", "dummy")
_REQUIRED_METADATA_FIELDS = ("module",)
_TABLE_OR_VIEW_METADATA_FIELDS = ("table", "view")
_IDENTITY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EntityExtractionStats:
    node_count: int
    occurrence_count: int
    diagnostic_count: int


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str | None
    start: int
    end: int
    valid: bool = True
    interpolation: bool = False


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


@dataclass
class _Assignment:
    key: str | None
    key_valid: bool
    field: str | None
    field_valid: bool
    start: int
    rhs_start: int
    rhs_end: int
    rhs: list[_Token]
    identity: bool


@dataclass
class _Declaration:
    path: str
    file_id: int
    source_sha: str
    text: str
    key: str
    span: _Span
    assignments: list[_Assignment] = field(default_factory=list)
    literal_values: dict[str, list[object]] = field(
        default_factory=lambda: {name: [] for name in _METADATA_FIELDS}
    )
    dynamic_fields: set[str] = field(default_factory=set)
    references: list[str] | None = None
    reference_kind: str | None = None
    reference_span: _Span | None = None


@dataclass(frozen=True)
class _Diagnostic:
    file_id: int
    source_key: str
    code: str
    message: str
    span: _Span


@dataclass
class _ParsedFile:
    path: str
    file_id: int
    source_sha: str
    text: str
    declarations: dict[str, _Declaration] = field(default_factory=dict)
    diagnostics: list[_Diagnostic] = field(default_factory=list)
    includes: list[tuple[str | None, bool, _Span]] = field(default_factory=list)
    lexical_invalid: bool = False


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _missing_required_metadata(values: dict[str, object | None]) -> list[str]:
    missing = [field for field in _REQUIRED_METADATA_FIELDS if values[field] is None]
    if all(values[field] is None for field in _TABLE_OR_VIEW_METADATA_FIELDS):
        missing.extend(_TABLE_OR_VIEW_METADATA_FIELDS)
    return missing


def _location(text: str, index: int) -> tuple[int, int]:
    before = text[:index]
    line = before.count("\n") + 1
    column = len(before.rsplit("\n", 1)[-1]) + 1
    return line, column


def _evidence(path: str, text: str, span: _Span) -> str:
    start_line, start_column = _location(text, span.start)
    end_line, end_column = _location(text, span.end)
    return _canonical(
        {
            "path": path,
            "start_line": start_line,
            "start_column": start_column,
            "end_line": end_line,
            "end_column": end_column,
            "text": text[span.start : span.end],
        }
    )


def _eof_span(text: str) -> _Span:
    return _Span(len(text), len(text))


def _read_quoted(text: str, start: int) -> tuple[int, str, bool, bool] | None:
    quote = text[start]
    value: list[str] = []
    valid = True
    interpolation = False
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == quote:
            return index + 1, "".join(value), valid, interpolation
        if char == "\\":
            if index + 1 >= len(text):
                return None
            escaped = text[index + 1]
            if escaped not in {"'", '"', "\\"}:
                valid = False
                value.extend(("\\", escaped))
            else:
                value.append(escaped)
            index += 2
            continue
        if quote == '"' and char == "$":
            interpolation = True
        value.append(char)
        index += 1
    return None


def _skip_heredoc(text: str, start: int) -> int | None:
    line_end = text.find("\n", start)
    if line_end < 0:
        return None
    header = text[start + 3 : line_end].strip()
    if len(header) >= 2 and header[0] in {"'", '"'} and header[-1] == header[0]:
        label = header[1:-1]
    else:
        label = header
    if not _IDENTITY_RE.fullmatch(label):
        return None
    cursor = line_end + 1
    while cursor <= len(text):
        next_end = text.find("\n", cursor)
        if next_end < 0:
            next_end = len(text)
        line = text[cursor:next_end].rstrip("\r")
        if line == label or line == f"{label};":
            return next_end + (1 if next_end < len(text) else 0)
        if next_end >= len(text):
            break
        cursor = next_end + 1
    return None


def _lex(text: str) -> tuple[list[_Token], int | None]:
    tokens: list[_Token] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "#" or (char == "/" and index + 1 < len(text) and text[index + 1] in {"/", "*"}):
            if char == "#" or text[index + 1] == "/":
                newline = text.find("\n", index)
                index = len(text) if newline < 0 else newline + 1
                continue
            end = text.find("*/", index + 2)
            if end < 0:
                return tokens, index
            index = end + 2
            continue
        if text.startswith("<<<", index):
            end = _skip_heredoc(text, index)
            if end is None:
                return tokens, index
            index = end
            continue
        if char in {"'", '"'}:
            parsed = _read_quoted(text, index)
            if parsed is None:
                return tokens, index
            end, value, valid, interpolation = parsed
            tokens.append(_Token("string", value, index, end, valid, interpolation))
            index = end
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*", text[index:])
            if match:
                end = index + len(match.group(0))
                tokens.append(_Token("var", match.group(0)[1:], index, end))
                index = end
                continue
        if char.isalpha() or char == "_":
            match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
            assert match is not None
            end = index + len(match.group(0))
            tokens.append(_Token("ident", match.group(0), index, end))
            index = end
            continue
        if char.isdigit():
            match = re.match(r"[0-9]+(?:\.[0-9]+)?", text[index:])
            assert match is not None
            end = index + len(match.group(0))
            tokens.append(_Token("number", match.group(0), index, end))
            index = end
            continue
        if text.startswith("::", index):
            tokens.append(_Token("punct", "::", index, index + 2))
            index += 2
            continue
        if text.startswith("=>", index):
            tokens.append(_Token("punct", "=>", index, index + 2))
            index += 2
            continue
        tokens.append(_Token("punct", char, index, index + 1))
        index += 1
    return tokens, None


def _delimiter_state(tokens: list[_Token]) -> tuple[list[tuple[int, int, int]], int | None]:
    states: list[tuple[int, int, int]] = []
    stack: list[str] = []
    opening = {"(": "(", "[": "[", "{": "{"}
    closing = {")": "(", "]": "[", "}": "{"
    }
    for token in tokens:
        states.append((stack.count("("), stack.count("["), stack.count("{")))
        if token.value in opening:
            stack.append(str(token.value))
        elif token.value in closing:
            if not stack or stack[-1] != closing[str(token.value)]:
                return states, token.start
            stack.pop()
    return states, (tokens[-1].end if stack and tokens else 0 if stack else None)


def _statement_end(tokens: list[_Token], states: list[tuple[int, int, int]], start: int) -> int:
    baseline = states[start] if start < len(states) else (0, 0, 0)
    for index in range(start, len(tokens)):
        if tokens[index].value == ";" and states[index] == baseline:
            return index
    return len(tokens)


def _matching_close(tokens: list[_Token], start: int) -> int | None:
    opener = tokens[start].value
    closer = {"[": "]", "(": ")", "{": "}"}.get(str(opener))
    if closer is None:
        return None
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index].value == opener:
            depth += 1
        elif tokens[index].value == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _parse_lhs(
    tokens: list[_Token], states: list[tuple[int, int, int]], index: int
) -> tuple[str | None, bool, str | None, bool, int] | None:
    if tokens[index].kind != "var" or tokens[index].value != "kSchemas":
        return None
    if index + 3 >= len(tokens) or tokens[index + 1].value != "[":
        return None
    key_token = tokens[index + 2]
    if tokens[index + 3].value != "]":
        return None
    key_valid = key_token.kind == "string" and key_token.valid and not key_token.interpolation
    key = key_token.value if key_valid else None
    next_index = index + 4
    field: str | None = None
    field_valid = True
    if next_index < len(tokens) and tokens[next_index].value == "[":
        if next_index + 2 >= len(tokens) or tokens[next_index + 2].value != "]":
            return key, key_valid, None, False, next_index
        field_token = tokens[next_index + 1]
        field_valid = (
            field_token.kind == "string"
            and field_token.valid
            and not field_token.interpolation
        )
        field = str(field_token.value).lower() if field_valid else None
        next_index += 3
    if next_index >= len(tokens) or tokens[next_index].value != "=":
        return None
    if states[index] != (0, 0, 0):
        return None
    return key, key_valid, field, field_valid, next_index


def _parse_array_metadata(rhs: list[_Token]) -> dict[str, list[tuple[object | None, bool]]]:
    if not rhs:
        return {}
    opener_index = 0
    if rhs[0].value == "[":
        closer = _matching_close(rhs, 0)
    elif rhs[0].kind == "ident" and str(rhs[0].value).lower() == "array" and len(rhs) > 1 and rhs[1].value == "(":
        opener_index = 1
        closer = _matching_close(rhs, opener_index)
    else:
        return {}
    if closer is None or closer != len(rhs) - 1:
        return {}
    result: dict[str, list[tuple[object | None, bool]]] = {}
    cursor = opener_index + 1
    while cursor < closer:
        if rhs[cursor].value == ",":
            cursor += 1
            continue
        key_token = rhs[cursor]
        if cursor + 2 >= closer or rhs[cursor + 1].value != "=>":
            cursor += 1
            continue
        field_valid = key_token.kind == "string" and key_token.valid and not key_token.interpolation
        field = str(key_token.value).lower() if field_valid else ""
        value_start = cursor + 2
        value_end = value_start
        stack: list[str] = []
        while value_end < closer:
            value = rhs[value_end].value
            if value in {"(", "[", "{"}:
                stack.append(str(value))
            elif value in {")", "]", "}"}:
                if stack:
                    stack.pop()
            elif value == "," and not stack:
                break
            value_end += 1
        value_token = rhs[value_start] if value_start < value_end else None
        if field not in _METADATA_FIELDS:
            cursor = value_end + 1
            continue
        if field in {"module", "table", "view"}:
            valid = value_token is not None and value_token.kind == "string" and value_token.valid and not value_token.interpolation and bool(value_token.value) and value_end == value_start + 1
            result.setdefault(field, []).append((value_token.value if valid else None, valid))
        else:
            valid = value_token is not None and value_token.kind == "ident" and value_token.value in {"true", "false"} and value_end == value_start + 1
            result.setdefault(field, []).append(((value_token.value == "true") if valid and value_token is not None else None, valid))
        cursor = value_end + 1
    return result


def _direct_reference(rhs: list[_Token]) -> tuple[str | None, str | None]:
    if len(rhs) == 4 and rhs[0].kind == "var" and rhs[0].value == "kSchemas" and rhs[1].value == "[" and rhs[3].value == "]":
        token = rhs[2]
        if token.kind == "string" and token.valid and not token.interpolation and token.value:
            return str(token.value), "direct"
        return None, "dynamic"
    if any(token.kind == "var" and token.value == "kSchemas" for token in rhs):
        return None, "dynamic"
    return None, None


def _is_empty_array(tokens: list[_Token]) -> bool:
    return (
        (len(tokens) == 2 and tokens[0].value == "[" and tokens[1].value == "]")
        or (
            len(tokens) == 3
            and tokens[0].kind == "ident"
            and str(tokens[0].value).lower() == "array"
            and tokens[1].value == "("
            and tokens[2].value == ")"
        )
    )


def _is_null(tokens: list[_Token]) -> bool:
    return len(tokens) == 1 and tokens[0].kind == "ident" and str(tokens[0].value).lower() == "null"


def _is_destination_fallback(tokens: list[_Token], destination: str) -> bool:
    for index in range(len(tokens) - 1):
        if tokens[index].value != "?" or tokens[index + 1].value != "?":
            continue
        key, kind = _direct_reference(tokens[:index])
        if kind == "direct" and key == destination and _is_empty_array(tokens[index + 2 :]):
            return True
    return False


def _inherit_references(rhs: list[_Token], destination: str | None = None) -> tuple[list[str] | None, str | None]:
    if len(rhs) < 4 or rhs[0].kind != "ident" or rhs[0].value != "EntityManager" or rhs[1].value != "::" or rhs[2].kind != "ident" or rhs[2].value != "inheritEnts" or rhs[3].value != "(":
        return None, None
    close = _matching_close(rhs, 3)
    if close is None or close != len(rhs) - 1:
        return None, "dynamic"
    args: list[list[_Token]] = []
    current: list[_Token] = []
    depth = 0
    for token in rhs[4:close]:
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")", "]", "}"}:
            depth -= 1
        if token.value == "," and depth == 0:
            args.append(current)
            current = []
        else:
            current.append(token)
    if current or args:
        args.append(current)
    if not args:
        return None, "dynamic"
    base, base_kind = _direct_reference(args[0])
    if base_kind != "direct" or base is None:
        return None, "dynamic"
    references = [base]
    for argument in args[1:]:
        if _is_empty_array(argument) or _is_null(argument):
            continue
        override, override_kind = _direct_reference(argument)
        if override_kind == "direct":
            if destination is not None and override == destination:
                continue
            if override is not None:
                references.append(override)
                continue
        if destination is not None and _is_destination_fallback(argument, destination):
            continue
        return [base], "dynamic"
    return references, "inherit"


def _parse_file(path: str, file_id: int, source_sha: str, text: str) -> _ParsedFile:
    parsed = _ParsedFile(path, file_id, source_sha, text)
    tokens, lexical_error = _lex(text)
    states, delimiter_error = _delimiter_state(tokens)
    if lexical_error is not None or delimiter_error is not None:
        parsed.lexical_invalid = True
        parsed.diagnostics.append(
            _Diagnostic(
                file_id,
                "",
                "entity_identity_invalid",
                _canonical({"reason": "invalid lexical state"}),
                _Span(lexical_error if lexical_error is not None else delimiter_error or len(text), lexical_error if lexical_error is not None else delimiter_error or len(text)),
            )
        )
        return parsed

    assignments: list[_Assignment] = []
    for index, token in enumerate(tokens):
        if token.kind == "var" and token.value == "kSchemas" and states[index] == (0, 0, 0):
            lhs = _parse_lhs(tokens, states, index)
            if lhs is None:
                continue
            key, key_valid, field, field_valid, equal_index = lhs
            end_index = _statement_end(tokens, states, equal_index + 1)
            rhs_tokens = tokens[equal_index + 1 : end_index]
            rhs_start = rhs_tokens[0].start if rhs_tokens else tokens[equal_index].end
            rhs_end = rhs_tokens[-1].end if rhs_tokens else tokens[equal_index].end
            assignments.append(
                _Assignment(
                    key,
                    key_valid,
                    field,
                    field_valid,
                    token.start,
                    rhs_start,
                    rhs_end,
                    rhs_tokens,
                    field is None and field_valid,
                )
            )
            if key is None or not key_valid or key == "":
                parsed.diagnostics.append(
                    _Diagnostic(file_id, "", "entity_identity_invalid", _canonical({"reason": "invalid identity key"}), _Span(token.start, rhs_end))
                )
                continue
            if field is not None and (not field_valid or field not in _METADATA_FIELDS):
                continue
            if field is None and field_valid:
                declaration = parsed.declarations.get(key)
                if declaration is None:
                    declaration = _Declaration(path, file_id, source_sha, text, key, _Span(token.start, rhs_end))
                    parsed.declarations[key] = declaration
                declaration.assignments.append(assignments[-1])
                for field_name, values in _parse_array_metadata(rhs_tokens).items():
                    for value, valid in values:
                        if valid:
                            declaration.literal_values[field_name].append(value)
                        else:
                            declaration.dynamic_fields.add(field_name)
                references, reference_kind = _inherit_references(rhs_tokens, key)
                if reference_kind is None:
                    references, reference_kind = _direct_reference(rhs_tokens)
                declaration.references = [references] if reference_kind == "direct" and references is not None else references
                declaration.reference_kind = reference_kind
                declaration.reference_span = _Span(rhs_start, rhs_end) if reference_kind is not None else None

    # Apply direct nested updates after all identity assignments have been
    # indexed, so source-shaped files may update a key before its declaration.
    for assignment in assignments:
        if assignment.field not in _METADATA_FIELDS or assignment.key is None:
            continue
        declaration = parsed.declarations.get(assignment.key)
        if declaration is None:
            continue
        value = assignment.rhs[0] if len(assignment.rhs) == 1 else None
        field = str(assignment.field)
        if field in {"module", "table", "view"}:
            valid = value is not None and value.kind == "string" and value.valid and not value.interpolation and bool(value.value)
            if valid:
                declaration.literal_values[field].append(value.value)
            else:
                declaration.dynamic_fields.add(field)
        else:
            valid = value is not None and value.kind == "ident" and value.value in {"true", "false"}
            if valid:
                declaration.literal_values[field].append(value.value == "true")
            else:
                declaration.dynamic_fields.add(field)

    for index, token in enumerate(tokens):
        if token.kind != "ident" or token.value not in {"include", "include_once", "require", "require_once"}:
            continue
        end_index = _statement_end(tokens, states, index + 1)
        expression = tokens[index + 1 : end_index]
        if expression and expression[0].value == "(" and expression[-1].value == ")":
            expression = expression[1:-1]
        if len(expression) == 1 and expression[0].kind == "string" and expression[0].valid and not expression[0].interpolation:
            parsed.includes.append((str(expression[0].value), True, _Span(token.start, tokens[end_index].end if end_index < len(tokens) else (expression[-1].end if expression else token.end))))
        else:
            parsed.includes.append((None, False, _Span(token.start, tokens[end_index].end if end_index < len(tokens) else token.end)))

    if not parsed.declarations:
        parsed.diagnostics.append(
            _Diagnostic(file_id, "", "entity_identity_missing", _canonical({"reason": "no valid top-level identity declaration"}), _eof_span(text))
        )
    return parsed


def _merge_literal_values(values: list[object]) -> tuple[object | None, bool]:
    if not values:
        return None, False
    distinct = list(dict.fromkeys(values))
    if len(distinct) == 1:
        return distinct[0], False
    return None, True


def _resolve_entities(
    parsed_files: dict[str, _ParsedFile],
) -> tuple[list[_Diagnostic], dict[tuple[int, str], dict[str, object | None]]]:
    declarations = [declaration for parsed in parsed_files.values() for declaration in parsed.declarations.values()]
    by_key: dict[str, list[_Declaration]] = {}
    for declaration in declarations:
        by_key.setdefault(declaration.key, []).append(declaration)
    diagnostics: list[_Diagnostic] = []
    resolved: dict[tuple[int, str], dict[str, object | None]] = {}
    failed_resolutions: set[tuple[int, str]] = set()
    cycle_members: set[tuple[int, str]] = set()
    reported_cycle_members: set[tuple[int, str]] = set()
    declarations_by_identity = {(declaration.file_id, declaration.key): declaration for declaration in declarations}
    resolving: list[tuple[int, str]] = []

    def resolve(declaration: _Declaration) -> dict[str, object | None]:
        identity = (declaration.file_id, declaration.key)
        if identity in resolved:
            return resolved[identity]
        if identity in resolving:
            cycle_start = resolving.index(identity)
            detected_members = resolving[cycle_start:]
            cycle_members.update(detected_members)
            failed_resolutions.update(detected_members)
            for cycle_identity in detected_members:
                if cycle_identity in reported_cycle_members:
                    continue
                cycle_declaration = declarations_by_identity[cycle_identity]
                cycle_declaration.reference_kind = "cycle"
                diagnostics.append(_Diagnostic(cycle_declaration.file_id, cycle_declaration.key, "entity_reference_cycle", _canonical({"destination": cycle_declaration.key}), cycle_declaration.reference_span or cycle_declaration.span))
                reported_cycle_members.add(cycle_identity)
            return {name: None for name in _METADATA_FIELDS}
        resolving.append(identity)
        values: dict[str, object | None] = {name: None for name in _METADATA_FIELDS}
        inherited_conflicts: set[str] = set()
        reference_failed = False
        if declaration.reference_kind == "dynamic":
            diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_reference_dynamic", _canonical({"destination": declaration.key}), declaration.reference_span or declaration.span))
            reference_failed = True
        if declaration.reference_kind != "dynamic" and declaration.references is not None:
            for source_key in declaration.references:
                candidates = by_key.get(source_key, [])
                if not candidates:
                    diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_reference_missing", _canonical({"destination": declaration.key, "source": source_key}), declaration.reference_span or declaration.span))
                    reference_failed = True
                    continue
                if len(candidates) != 1:
                    diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_reference_ambiguous", _canonical({"destination": declaration.key, "source": source_key}), declaration.reference_span or declaration.span))
                    reference_failed = True
                    continue
                source_values = resolve(candidates[0])
                source_identity = (candidates[0].file_id, candidates[0].key)
                if source_identity in failed_resolutions:
                    reference_failed = True
                for field_name in _METADATA_FIELDS:
                    source_value = source_values[field_name]
                    if source_value is None:
                        continue
                    if field_name in inherited_conflicts:
                        continue
                    if values[field_name] is not None and values[field_name] != source_value:
                        values[field_name] = None
                        inherited_conflicts.add(field_name)
                    else:
                        values[field_name] = source_value
        for field_name in _METADATA_FIELDS:
            literal_value, conflict = _merge_literal_values(declaration.literal_values[field_name])
            if field_name in declaration.dynamic_fields:
                values[field_name] = None
                if conflict:
                    diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_metadata_conflict", _canonical({"field": field_name, "kind": "direct"}), declaration.span))
                diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_metadata_dynamic", _canonical({"fields": [field_name]}), declaration.span))
            elif conflict:
                values[field_name] = None
                diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_metadata_conflict", _canonical({"field": field_name, "kind": "direct"}), declaration.span))
            elif declaration.literal_values[field_name]:
                values[field_name] = literal_value
            elif field_name in inherited_conflicts:
                values[field_name] = None
                diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_metadata_conflict", _canonical({"field": field_name, "kind": "inheritance"}), declaration.reference_span or declaration.span))
        missing = _missing_required_metadata(values)
        if missing and not reference_failed:
            diagnostics.append(_Diagnostic(declaration.file_id, declaration.key, "entity_metadata_missing", _canonical({"missing": missing}), declaration.span))
        resolving.pop()
        if reference_failed:
            failed_resolutions.add(identity)
        resolved[identity] = values
        return values

    for declaration in declarations:
        resolve(declaration)
    return diagnostics, resolved


def _include_target(path: str, include_value: str, retained_paths: dict[str, list[int]]) -> tuple[str | None, str]:
    # Cross-directory stream_resolve_include_path targets remain intentional unresolved
    # diagnostics until an authoritative configured include-root manifest exists.
    # No basename fallback is allowed.
    if include_value.startswith("/"):
        return None, "missing"
    candidate = PurePosixPath(posixpath.normpath(posixpath.join(str(PurePosixPath(path).parent), include_value))).as_posix()
    if candidate == ".." or candidate.startswith("../"):
        return None, "missing"
    matches = retained_paths.get(candidate, [])
    if not matches:
        return None, "missing"
    if len(matches) != 1:
        return candidate, "ambiguous"
    return candidate, "ok"


def _include_basename_candidates(
    include_value: str, retained_basename_paths: dict[str, list[str]]
) -> list[str]:
    """Return evidence-only candidates without using basename resolution."""
    return list(retained_basename_paths.get(PurePosixPath(include_value).name, ()))


def _insert_diagnostic(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    file_id: int,
    source_key: str,
    code: str,
    message: str,
    source_sha: str,
    evidence: str,
    occurrence_id: int | None,
) -> None:
    payload = {"repo_id": repo_id, "file_id": file_id, "source_key": source_key, "code": code, "evidence": evidence}
    diagnostic_key = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT OR IGNORE INTO entity_diagnostics(
               repo_id,file_id,source_key,occurrence_id,diagnostic_key,severity,
               code,message,source_commit_sha,evidence,extractor
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (repo_id, file_id, source_key or None, occurrence_id, diagnostic_key, "error", code, message, source_sha, evidence, ENTITY_EXTRACTOR),
    )


def extract_snapshot_entity_occurrences(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> EntityExtractionStats:
    """Extract `.ent` declarations solely from a materialized target snapshot."""
    conn.execute("DELETE FROM entity_diagnostics WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM entity_occurrences WHERE repo_id=?", (repo_id,))
    file_rows = {
        str(row["path"]): row
        for row in conn.execute("SELECT id,path,source_commit_sha FROM files WHERE repo_id=?", (repo_id,)).fetchall()
    }
    snapshot_entries = {entry.path: entry for entry in snapshot.entries}
    ent_paths: dict[str, list[int]] = {
        path: [int(row["id"])]
        for path, row in file_rows.items()
        if path.endswith(".ent")
    }
    retained_paths: dict[str, list[int]] = {
        path: [int(row["id"])]
        for path, row in file_rows.items()
        if path in snapshot_entries
    }
    retained_basename_paths: dict[str, list[str]] = {}
    for path in retained_paths:
        retained_basename_paths.setdefault(PurePosixPath(path).name, []).append(path)
    for paths in retained_basename_paths.values():
        paths.sort()
    parsed_files: dict[str, _ParsedFile] = {}
    all_diagnostics: list[_Diagnostic] = []
    for path in sorted(ent_paths):
        row = file_rows[path]
        if path not in snapshot_entries:
            raise SourceSnapshotError(f"snapshot entry is not present in candidate files: {path}")
        try:
            source = (snapshot.snapshot_root / Path(path)).read_bytes()
            text = source.decode("utf-8")
        except SourceSnapshotError:
            raise
        except Exception as exc:  # per-file source read failures are retained as diagnostics  # noqa: BLE001
            all_diagnostics.append(_Diagnostic(int(row["id"]), "", "entity_source_read_error", str(exc), _Span(0, 0)))
            continue
        try:
            parsed = _parse_file(path, int(row["id"]), str(row["source_commit_sha"]), text)
        except Exception as exc:  # parser failures are non-blocking diagnostics  # noqa: BLE001
            parsed = _ParsedFile(path, int(row["id"]), str(row["source_commit_sha"]), text)
            parsed.diagnostics.append(_Diagnostic(int(row["id"]), "", "entity_identity_invalid", str(exc), _eof_span(text)))
        parsed_files[path] = parsed
        all_diagnostics.extend(parsed.diagnostics)

    # Includes are resolved against the retained inventory only.  The source
    # declarations are indexed independently, so an included file contributes
    # its own source-backed occurrence rather than being merged into its caller.
    include_graph: dict[str, list[tuple[str, _Span]]] = {}
    for path, parsed in parsed_files.items():
        for include_value, literal, span in parsed.includes:
            if not literal or include_value is None:
                all_diagnostics.append(_Diagnostic(parsed.file_id, "", "entity_include_dynamic", _canonical({"path": path}), span))
                continue
            target, state = _include_target(path, include_value, retained_paths)
            if state == "ok" and target is not None:
                if target in parsed_files:
                    include_graph.setdefault(path, []).append((target, span))
            else:
                code = "entity_include_ambiguous" if state == "ambiguous" else "entity_include_missing"
                message: dict[str, object] = {"include": include_value}
                if state == "missing" and "/" not in include_value:
                    candidates = _include_basename_candidates(
                        include_value, retained_basename_paths
                    )
                    if candidates:
                        code = "entity_include_unresolved"
                        message["candidates"] = candidates
                all_diagnostics.append(
                    _Diagnostic(parsed.file_id, "", code, _canonical(message), span)
                )

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(path: str) -> None:
        if path in visiting:
            return
        if path in visited:
            return
        visiting.append(path)
        for target, span in include_graph.get(path, []):
            if target in visiting:
                parsed = parsed_files[path]
                all_diagnostics.append(_Diagnostic(parsed.file_id, "", "entity_include_cycle", _canonical({"include": target}), span))
            else:
                visit(target)
        visiting.pop()
        visited.add(path)

    for path in sorted(parsed_files):
        visit(path)

    resolution_diagnostics, resolved = _resolve_entities(parsed_files)
    all_diagnostics.extend(resolution_diagnostics)
    declarations = [declaration for parsed in parsed_files.values() for declaration in parsed.declarations.values()]
    declarations.sort(key=lambda declaration: (declaration.path, declaration.key))
    occurrence_ids: dict[tuple[int, str], int] = {}
    for declaration in declarations:
        node = conn.execute("INSERT OR IGNORE INTO entity_nodes(name) VALUES(?)", (declaration.key,))
        if node.rowcount == 0:
            node_row = conn.execute("SELECT id FROM entity_nodes WHERE name=?", (declaration.key,)).fetchone()
        else:
            node_row = (node.lastrowid,)
        assert node_row is not None
        values = resolved[(declaration.file_id, declaration.key)]
        evidence = _evidence(declaration.path, declaration.text, declaration.span)
        occurrence_id = int(conn.execute(
            """INSERT INTO entity_occurrences(
                   repo_id,entity_id,source_file_id,source_key,module,table_name,view_name,
                   dummy,source_commit_sha,evidence,extractor
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (repo_id, int(node_row[0]), declaration.file_id, declaration.key, values["module"], values["table"], values["view"], None if values["dummy"] is None else int(bool(values["dummy"])), declaration.source_sha, evidence, ENTITY_EXTRACTOR),
        ).lastrowid)
        occurrence_ids[(declaration.file_id, declaration.key)] = occurrence_id

    file_paths_by_id = {int(row["id"]): path for path, row in file_rows.items()}
    all_diagnostics.sort(key=lambda diagnostic: (file_paths_by_id.get(diagnostic.file_id, ""), diagnostic.source_key, diagnostic.code, diagnostic.span.start, diagnostic.message))
    for diagnostic in all_diagnostics:
        parsed = parsed_files.get(file_paths_by_id.get(diagnostic.file_id, ""))
        source_text = parsed.text if parsed is not None else ""
        evidence = _evidence(file_paths_by_id.get(diagnostic.file_id, ""), source_text, diagnostic.span)
        occurrence_id = occurrence_ids.get((diagnostic.file_id, diagnostic.source_key))
        source_sha = str(file_rows[ file_paths_by_id[diagnostic.file_id] ]["source_commit_sha"])
        _insert_diagnostic(conn, repo_id=repo_id, file_id=diagnostic.file_id, source_key=diagnostic.source_key, code=diagnostic.code, message=diagnostic.message, source_sha=source_sha, evidence=evidence, occurrence_id=occurrence_id)

    node_count = int(conn.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0])
    occurrence_count = int(conn.execute("SELECT COUNT(*) FROM entity_occurrences WHERE repo_id=?", (repo_id,)).fetchone()[0])
    diagnostic_count = int(conn.execute("SELECT COUNT(*) FROM entity_diagnostics WHERE repo_id=?", (repo_id,)).fetchone()[0])
    return EntityExtractionStats(node_count, occurrence_count, diagnostic_count)
