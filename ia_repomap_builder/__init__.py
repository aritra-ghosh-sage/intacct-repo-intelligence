"""Intacct repository-context evaluation helpers.

The package deliberately keeps the engine boundary small.  A builder may use
the lexical baseline, Aider RepoMap, or a Ripwire binary, but every engine
returns the same result contract and reports unavailable capabilities instead
of inventing context.
"""

from .benchmark import EvaluationTask, evaluate, load_tasks, score_task
from .builder import build
from .config import PHP_FAMILY_EXTENSIONS, BuildRequest, BuildResult, ContextItem

__all__ = [
    "BuildRequest",
    "ContextItem",
    "BuildResult",
    "PHP_FAMILY_EXTENSIONS",
    "build",
    "EvaluationTask",
    "evaluate",
    "load_tasks",
    "score_task",
]
