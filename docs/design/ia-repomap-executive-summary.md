# `ia_repomap` executive summary

Date: 2026-09-29  
Scope: the current `ia_repomap_builder` implementation in this repository.  
Status: local implementation; production availability is not established by
the repository evidence.

## Why the project exists

The project was created to give coding agents and reviewers useful context
about Intacct source changes. The underlying research identified three needs
for pull request (PR) review: summarize the change, find code that might be
affected, and suggest relevant tests. Freeform code exploration can miss
relationships when time or output limits are reached, so the design combines
bounded repository-map queries with explicit evidence and gaps.
[Sources: project purpose](ia-repomap-context-contract.md#purpose-and-boundaries),
[problem framing](llm-agent-pr-blast-radius-research.md#1-problem-framing),
[approach](llm-agent-pr-blast-radius-research.md).

The intended benefit is a traceable starting point for human review. The
repository does not establish a measured reduction in review time, defects,
or test effort.

## What it does today

This is a local, self-hosted workflow. A participating repository supplies a
small configuration marker. Analysis uses an exact, clean PR-head checkout
and base revision; the `review` command can acquire the checkout from a
caller-owned repository and PR URL. The project prepares a Ripwire index
outside the target repository and checks that it matches the checkout.
Ripwire is the static repository-map engine. Strands is the framework for the
optional agent workflow through Amazon Bedrock.
[Sources: contract](ia-repomap-context-contract.md#repository-declaration),
[readiness](ia-repomap-context-contract.md#revision-bound-artifacts),
[review workflow](../../ia_repomap_builder/README.md#running-pr-context-impact-and-analysis).

| Capability | Current implementation |
| --- | --- |
| Repository context | Ranks task-relevant files and symbols. The main PR workflow maps Intacct PHP-family files under `app/source`. |
| PR change context | Uses Git for changed files and hunks, then maps in-scope hunks to candidate symbols and direct callers. It reports missing, ambiguous, or truncated evidence. |
| Impact exploration | Expands a selected candidate symbol through bounded Ripwire queries. Returned relationships are lower-bound candidates, not proven runtime impact. |
| PR analysis | A local Strands coordinator can use Amazon Bedrock to produce a structured PR summary, candidate blast radius, and suggested test areas. Host code controls evidence, limits, revision checks, and the report. |
| Review output | A self-hosted `review` command can set up an exact PR checkout and write revision-bound JSON and Markdown reports outside the target repository. The host-owned outcome calls for manual review or reports that analysis is unavailable. |
| Test information | Ripwire test paths and an optional caller-supplied test inventory can identify candidate test areas and gaps. Suggested tests are marked `not_run`; the workflow does not run tests or establish passed continuous integration (CI) coverage. |

[Sources: implementation status](ia-repomap-context-contract.md#execution-status),
[local workflow](../../ia_repomap_builder/README.md#running-pr-context-impact-and-analysis),
[coordinator boundary](strands-pr-analysis-coordinator.md#purpose),
[research status](llm-agent-pr-blast-radius-research.md#current-implementation-status).

Here, **candidate** means a lead that still needs verification; **lower
bound** means the result may omit affected code.

The map covers `app/source`; the review report retains Git evidence for
changed files outside that scope, including `app/db`, with an explicit manual
review limitation. The project is a review aid: it does not approve or reject
PRs, execute tests, or prove that all affected code was found.
[Sources: scope and limitations](../../ia_repomap_builder/README.md#running-pr-context-impact-and-analysis),
[coordinator boundary](strands-pr-analysis-coordinator.md#purpose).

## Roadmap: documented future work

The repository records deferred work and evaluation gaps, but no approved
sequence, owner, delivery date, or production rollout plan. The items below
are **documented directions, not committed milestones**.

| Direction | Recorded status or gap |
| --- | --- |
| Measure the approach | Build a known-answer evaluation set and compare lexical, Aider, and Ripwire results. The documented bakeoff has not run. |
| Validate wider use | The repository records local PR evidence and a local coordinator, while live Bedrock proof and general target-repository onboarding remain unestablished here. |
| Improve impact and test evidence | Static analysis can miss dynamic or configuration-driven relationships. Executed test and CI coverage are outside the current report. |
| Add product integrations | Hosted PR/CI operation, GitHub publication, Model Context Protocol (MCP), editor, coding-harness, and installable command-line integration are deferred. |

[Sources: research status](llm-agent-pr-blast-radius-research.md#current-implementation-status),
[deferred work](ia-repomap-context-contract.md#assumptions-and-deferred-work),
[coordinator boundary](strands-pr-analysis-coordinator.md#iteration-record).

An approved roadmap would need a decision on which of these directions to
prioritize, plus owners and success criteria. This summary does not assign
those decisions.
