# CodeGraph and agent token usage — executive study brief

Date: 2026-10-01  
Scope: Installed CodeGraph usage and available token-efficiency evidence for
this repository. Local agent-vs-control measurements have not been run.

## Executive summary

CodeGraph gives coding agents a prebuilt map of symbols and relationships, so
they can retrieve relevant code and caller paths without manually traversing
files. That can reduce tool calls and total tokens processed. The retrieved
source still enters the model context, however, and a large result can occupy
context across later turns.

There is no measured token-savings result for this repository. The installed
CodeGraph telemetry is disabled, and its documented usage counters track
commands and tool calls, not model token counts. The upstream project reports
token reductions in its own benchmark, alongside a separate increase in
residual context occupancy. Those results are useful hypotheses, not a local
ROI estimate.

**Recommendation:** treat CodeGraph as a code-navigation aid and run a small,
paired evaluation on representative Intacct tasks before claiming savings or
expanding its use. Judge token cost and answer quality together.

## How agents use it

CodeGraph indexes a repository locally, then exposes retrieval operations to
the CLI and connected agents. The local CLI supports `status`, `query`,
`explore`, `context`, `node`, `callers`, `callees`, `impact`, and `affected`.
For example, `explore` returns relevant symbols, source, and call paths in one
response. `context` assembles task-relevant symbols and relationships. The
index is supporting navigation evidence; agents should verify important
conclusions against source, tests, and CI.

In this workspace, CodeGraph 1.6.0 reports an up-to-date index of 31 Python
files, 1,157 nodes, 3,615 edges, and 5.31 MB. This is a small repository
snapshot, not evidence about performance on the larger Intacct application.
This study used `codegraph explore` to locate project workflows and source;
the tool result included verbatim source excerpts and call-path information.

The repository's PR-analysis design is a separate workflow built around
revision-bound Ripwire evidence and one bounded Strands coordinator. Its
acceptance bakeoff compares Ripwire, Aider, and lexical context retrieval; it
does not currently report an evaluated CodeGraph-vs-control token result.

## What token usage means

CodeGraph itself does not call an LLM for ordinary local graph queries. Token
cost occurs when an agent receives CodeGraph's output and sends that content
to its model. Indexing time and storage are separate operational costs.

Track these quantities separately:

| Measure | What it answers |
|---|---|
| Input tokens, including cached input | How much prompt/context the model processed |
| Output tokens | How much model response was generated |
| Total processed tokens and provider cost | What the provider billed or counted across turns |
| Residual context occupancy | How much retrieved content remains in the context window for later turns |
| Tool calls and wall time | Whether retrieval reduces interaction and elapsed time |
| Correctness and evidence quality | Whether the agent found and explained the right code |

Total processed tokens and residual occupancy are different. A tool can reduce
repeated exploration over a whole task while leaving a larger result in the
context window between turns. Token estimates based only on response
characters are not a substitute for provider usage records.

## Evidence available today

| Evidence | Finding | How to interpret it |
|---|---|---|
| Local installation | Version 1.6.0; index is up to date (31 files, all Python) | Confirms availability and current index health only |
| Local telemetry setting | Disabled | No local CodeGraph usage counters are being sent; this study has no per-user usage history |
| CodeGraph telemetry design | Documented counters include command/tool invocations and errors, plus agent/version for MCP calls; token counts are not among documented fields | Even enabled telemetry would not answer token consumption |
| Upstream agent benchmark | A 2026-08-05, three-turn Claude Sonnet campaign across seven public repositories reports 56% fewer processed tokens and 84% fewer tool calls on average with CodeGraph; its residual-context measure is 82% higher on average | Vendor-published result under a particular harness and model; not an Intacct result |
| Existing Intacct evidence | The repo-map context bakeoff is listed as not run; live Bedrock and hosted integration are deferred | There is no local quality/cost comparison to support an ROI claim |

The upstream benchmark is especially useful because it reports both
throughput and context occupancy and documents measurement corrections and a
control-arm contamination issue. It is still authored by the CodeGraph
project, uses seven public repositories and a specific Claude setup, and does
not measure this team's tasks. Its averages should not be carried into an
executive savings forecast.

## Usefulness: what to expect and what to test

**Likely useful for:** locating definitions and callers, tracing relationships
across files, answering onboarding questions, and narrowing the next source
files an agent should inspect.

**Potential costs or failure modes:** broad exploration can return more source
than needed; copied source consumes model context; graph coverage is bounded by
the indexed language and static relationships; and an absent edge is not proof
that no runtime relationship exists. A stale index can also mislead, so index
freshness should be recorded for every run.

Usefulness is established only if the tool improves task outcomes: correct
files or symbols found, grounded explanations, time to a verified answer, or
fewer unnecessary retrieval steps. Fewer tokens alone are not sufficient if
the agent misses relevant code or produces weaker answers.

## Proposed local evaluation

Run a paired comparison on 30–50 representative tasks, spanning symbol
location, caller tracing, change-impact investigation, and repository
onboarding. Use the same model/version, task wording, repository revision,
context limits, and answer rubric in both arms. Randomize arm order and use
fresh sessions. Keep CodeGraph unavailable in the control arm, including via
CLI or shell, to prevent accidental crossover.

For each task, record:

1. Model/provider input, cached-input, and output tokens per turn, plus cost.
2. CodeGraph and file-search tool calls, wall time, index build/sync time, and
   index age.
3. Correct target files/symbols, citation accuracy, missed relevant evidence,
   and reviewer-rated answer usefulness.
4. Retrieved-response tokens or residual occupancy across turns where the
   agent platform exposes reliable measurements.

Report paired medians and ranges, not just a pooled average. Break results out
by task type and task difficulty. Report quality alongside token/cost
differences; separate one-time indexing and maintenance cost from per-task
usage. Do not use generated character counts as the primary token measure.

**Decision gate:** expand usage only if CodeGraph improves or preserves answer
quality and shows a repeatable benefit in at least one operational measure
(provider cost, total tokens, completion time, or tool-call load). Explain any
increase in residual context or indexing overhead in the same report.

## Sources and traceability

- Local status and CLI help: `codegraph version`, `codegraph status`,
  `codegraph help context`, `codegraph help explore`, and
  `codegraph telemetry status`, observed 2026-10-01.
- Repository design: [`ia-repomap-context-contract.md`](ia-repomap-context-contract.md),
  [`strands-pr-analysis-coordinator.md`](strands-pr-analysis-coordinator.md),
  and [`llm-agent-pr-blast-radius-research.md`](llm-agent-pr-blast-radius-research.md).
- CodeGraph's [telemetry field list](https://github.com/colbymchenry/codegraph/blob/main/TELEMETRY.md)
  and [residual-context benchmark](https://github.com/colbymchenry/codegraph/blob/main/docs/benchmarks/residual-context-occupancy.md),
  accessed 2026-10-01. External benchmark numbers are vendor-reported and are
  not reproduced here.
