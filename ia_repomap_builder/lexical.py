"""Small deterministic baseline used to measure the ready-made engines."""

from __future__ import annotations

import re
import time

from .config import BuildRequest, BuildResult, ContextItem
from .files import iter_files, relative_path

_DEFINITION = re.compile(
    r"\b(class|interface|trait|enum|function)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def build_lexical(request: BuildRequest) -> BuildResult:
    root = request.repo_root.resolve()
    started = time.perf_counter()
    items: list[ContextItem] = []
    files = 0
    parsed = 0
    skipped = 0
    extensions: dict[str, dict[str, int]] = {}
    query_tokens = {token.lower() for token in _TOKEN.findall(request.query or "")}

    for path in iter_files(root, request.scope):
        files += 1
        extension = path.suffix.lower()
        extension_stats = extensions.setdefault(extension, {"seen": 0, "parsed": 0, "skipped": 0})
        extension_stats["seen"] += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped += 1
            extension_stats["skipped"] += 1
            continue
        parsed += 1
        extension_stats["parsed"] += 1
        rel = relative_path(root, path)
        definitions = list(_DEFINITION.finditer(text))
        if not definitions:
            definitions = [None]
        for match in definitions:
            symbol = match.group(2) if match else None
            kind = match.group(1) if match else None
            haystack = f"{rel} {symbol or ''} {text}".lower()
            score = float(sum(haystack.count(token) for token in query_tokens))
            if request.query and score == 0:
                continue
            line = text.count("\n", 0, match.start()) + 1 if match else None
            items.append(
                ContextItem(
                    path=rel,
                    symbol=symbol,
                    kind=kind,
                    line=line,
                    score=score,
                    evidence="lexical-name-or-path-match",
                )
            )

    items.sort(key=lambda item: (-float(item.score or 0), item.path, item.line or 0, item.symbol or ""))
    selected: list[ContextItem] = []
    rendered_rows: list[str] = []
    for item in items:
        row = f"{item.path}:{item.line or 1} {item.kind or 'file'} {item.symbol or ''}".rstrip()
        candidate_rows = rendered_rows + [row]
        candidate_context = "\n".join(candidate_rows)
        if len(candidate_context) // 4 > request.token_budget:
            # A single oversized row is omitted rather than truncated: the
            # baseline must not manufacture an invalid partial symbol record.
            if not selected:
                continue
            break
        selected.append(item)
        rendered_rows = candidate_rows
    items = selected
    context = "\n".join(rendered_rows)
    return BuildResult(
        engine="lexical",
        status="ok",
        items=items,
        context=context,
        diagnostics=["lexical baseline does not resolve call/reference edges"],
        metrics={
            "files_seen": files,
            "files_parsed": parsed,
            "files_skipped": skipped,
            "extensions": extensions,
            "edges": 0,
            "estimated_tokens": max(0, len(context) // 4),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
