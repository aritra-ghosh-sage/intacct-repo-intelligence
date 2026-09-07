"""Public dispatcher and common identity handling."""

from __future__ import annotations

from .config import BuildRequest, BuildResult
from .engines import build_aider, build_ripwire
from .files import resolve_scopes
from .identity import cache_key, git_revision, is_dirty
from .lexical import build_lexical


def build(request: BuildRequest) -> BuildResult:
    root = request.repo_root.resolve()
    revision = request.revision or git_revision(root)
    dirty = is_dirty(root)
    identity = {
        "revision": revision,
        "dirty": dirty,
        "scope": list(request.scope),
        "engine": request.engine,
        "token_budget": request.token_budget,
        "cache_key": cache_key(
            root,
            request.scope,
            request.engine,
            "ia-repomap-builder-v1",
            request.token_budget,
            revision,
            dirty,
        ),
    }

    try:
        resolve_scopes(root, request.scope)
    except ValueError as exc:
        return BuildResult(request.engine, "error", diagnostics=[str(exc)], identity=identity)

    if request.token_budget <= 0:
        return BuildResult(request.engine, "error", diagnostics=["token_budget must be positive"], identity=identity)
    if request.engine == "lexical":
        result = build_lexical(request)
    elif request.engine == "aider":
        result = build_aider(request)
    elif request.engine == "ripwire":
        result = build_ripwire(request)
    else:
        result = BuildResult(
            request.engine,
            "error",
            diagnostics=[f"unknown engine: {request.engine}; expected lexical, aider, or ripwire"],
        )
    result.identity = identity
    return result
