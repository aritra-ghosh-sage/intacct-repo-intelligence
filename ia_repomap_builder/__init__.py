"""Intacct repository-context evaluation helpers.

The package deliberately keeps the engine boundary small.  A builder may use
the lexical baseline, Aider RepoMap, or a Ripwire binary, but every engine
returns the same result contract and reports unavailable capabilities instead
of inventing context.
"""

from .benchmark import EvaluationTask, evaluate, load_tasks, score_task
from .builder import build
from .config import (
    PHP_FAMILY_EXTENSIONS,
    BuildRequest,
    BuildResult,
    ContextItem,
    PrAffectedTestCandidate,
    PrCallerCandidate,
    PrChangedFile,
    PrContextGap,
    PrContextRequest,
    PrContextResult,
    PrImpactCandidate,
    PrImpactedFileCandidate,
    PrImpactRequest,
    PrImpactResult,
    PrepareRepoMapRequest,
    PrSymbolCandidate,
    RepoMapConfig,
)
from .pr_context import build_pr_context
from .impact import build_symbol_impact
from .pr_analysis import PRAnalysisReportV1, PRAnalysisRequestV1, run_pr_analysis
from .pr_analysis_skills import RipwireSkillPolicy, agent_skills_supported
from .readiness import (
    check_prepared_repomap_readiness,
    check_repomap_readiness,
    load_repomap_config,
    prepare_repomap,
)

__all__ = [
    "BuildRequest",
    "ContextItem",
    "BuildResult",
    "PHP_FAMILY_EXTENSIONS",
    "build",
    "RepoMapConfig",
    "PrepareRepoMapRequest",
    "PrContextRequest",
    "PrCallerCandidate",
    "PrAffectedTestCandidate",
    "PrSymbolCandidate",
    "PrChangedFile",
    "PrContextGap",
    "PrContextResult",
    "PrImpactRequest",
    "PrImpactCandidate",
    "PrImpactedFileCandidate",
    "PrImpactResult",
    "load_repomap_config",
    "prepare_repomap",
    "check_repomap_readiness",
    "check_prepared_repomap_readiness",
    "build_pr_context",
    "build_symbol_impact",
    "PRAnalysisRequestV1",
    "PRAnalysisReportV1",
    "RipwireSkillPolicy",
    "run_pr_analysis",
    "agent_skills_supported",
    "EvaluationTask",
    "evaluate",
    "load_tasks",
    "score_task",
]
