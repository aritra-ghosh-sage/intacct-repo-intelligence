# Strands PR-analysis coordinator implementation plan

Date: 2026-09-15
Status: **Implemented locally**
Scope: complete the local, deterministic Strands coordinator defined in
[`strands-pr-analysis-coordinator.md`](strands-pr-analysis-coordinator.md).

## Operational clarification

**Planning-only gate.** The execution controls were clarified before runtime
work began. The five slices are now implemented locally, remain uncommitted,
and are covered by the validation below. The plan is the source of truth. Any
unrelated worktree edits remain user-owned and must be preserved and reviewed
separately; they are not silently attributed to this plan.

**Scope lock.** Once implementation is authorized, changes are limited to the
files listed in `Files touched`. The target `ia-app` checkout, Ripwire
checkout, external caches, generated evidence, network state, and credentials
remain outside this plan. The adjacent Ripwire `SKILL.md` files are design and
test input only; they are not loaded or exposed by the coordinator.

**Per-slice gate.** Before each slice, capture repository status and verify its
declared inputs. Keep implementation, focused review, fixes, and re-verification
inside that slice. A slice is complete only when its definition of done and
focused tests pass, with the evidence recorded in the implementation handoff
or review notes. Do not mark a design or research document as implemented
early; update the canonical documents only in Slice 5 after runtime behavior
and tests pass.

**Failure boundary.** If the same slice fails for five consecutive agent turns,
stop and report the failed criterion, attempted commands, recurring failure,
changes already made, safe continuation or rollback options, and the exact
product, infrastructure, or user decision required. Do not broaden scope or
silently retry with a cold index, unrestricted shell, network operation, or
new integration.

**Final gate.** After all slices, the complete test suite, whitespace and
heading checks, and complete-diff review must pass, with only intended files
changed. Commit or publish only after a separate authorization; this plan has
created no commit.

## Goal

Complete the smallest usable workflow around the repository-map APIs that
already exist:

```text
validated request
  -> build_pr_context() once
  -> deterministic early return, or one Strands agent
  -> bounded symbol-impact and repository inspection
  -> post-run revision guard
  -> atomic external report bundle
```

Reuse the existing report models, evidence session, Bedrock configuration,
`build_pr_context()`, and `build_symbol_impact()`. Do not introduce another
graph, cache, agent framework, retry system, or policy engine.

## Decisions retained

- Use one Strands coordinator agent backed by Amazon Bedrock.
- Keep Ripwire as the repository-map engine.
- Require an exact, clean PR-head checkout and matching external index.
- Never prepare an index implicitly.
- Keep Git-confirmed files distinct from candidate static relationships.
- Retain raw Ripwire XML as canonical evidence and keep it out of prompts.
- Use JSON as the canonical report and render Markdown deterministically from
  it.
- Keep reports, XML, and indexes outside the target repository.
- Allow at most five symbol-impact calls and 20 rows per call.
- Allow at most two repository-inspection calls.
- Count rejected tool calls against the relevant limit.
- Keep the combined ceiling at seven model-callable tool requests.
- Record candidate test areas with `execution_status="not_run"`.
- Recheck the exact `HEAD` and clean-tree state after agent execution.
- Require explicit host opt-in before source excerpts may reach Bedrock.
- Use fake agents and evidence providers in the default tests.
- Keep live Bedrock validation optional and environment-gated.
- Do not add a CLI, MCP, editor, GitHub, publication, test-execution, or
  multi-agent integration.

## Ripwire skill boundary

The adjacent Ripwire checkout contains 18 Agent Skills-compatible `SKILL.md`
files, and the pinned Strands release can load them with `AgentSkills`.
However, the coordinator must not load those skills wholesale at runtime.

The skills target a general shell-capable coding agent. Most assume `Bash` and
`Read`, some describe mutation or test execution, and their `allowed-tools`
metadata is informational rather than an enforced Strands permission boundary.
The generic router also says PHP is unsupported, which conflicts with the
patched Intacct engine.

Use the relevant `ripwire-change-check` principles only as design and test
input:

- generate PR context once;
- expand only a selected candidate when needed;
- bind evidence to the exact revision;
- disclose ambiguity, truncation, and missing coverage;
- describe graph results as a lower bound.

Do not expose Bash, a generic Ripwire command tool, or the Ripwire MCP server.
Curated coordinator-specific skill profiles may be host-enabled as compact
guidance, but they do not grant tools or permissions and must remain
deterministically selected from workflow state.

## Public interface

Add and export the documented request model:

```python
class PRAnalysisRequestV1(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["ia-repomap.pr-analysis-request/v1"] = Field(
        alias="schema"
    )
    repo_root: Path
    base_ref: str
    artifact_root: Path
    output_dir: Path
```

Add one coordinator entry point:

```python
def run_pr_analysis(
    request: PRAnalysisRequestV1 | Mapping[str, Any],
    settings: BedrockSettings | None = None,
    *,
    allow_source_inspection: bool = False,
    context_builder=build_pr_context,
    impact_builder=build_symbol_impact,
    agent_factory=None,
) -> PRAnalysisReportV1:
    ...
```

The injectable callables are test seams, not another public abstraction.
The coordinator constructs PR context with the existing defaults:

```text
token_budget     repository declaration
limit            20
offset           0
history_commits  500
```

## Slice 1: request and deterministic early returns

### Input

A versioned request containing `repo_root`, `base_ref`, `artifact_root`, and
`output_dir`.

### Implementation

In `ia_repomap_builder/pr_analysis.py`:

1. Validate the schema, unknown keys, non-empty base, absolute paths, external
   output location, and output-directory state before repository-map access.
2. Construct one `PrContextRequest` and call `build_pr_context()` exactly once.
3. Produce complete reports without constructing Strands for these branches:

| Condition | Status | Phase |
| --- | --- | --- |
| Invalid request | `error` | `request_validation` |
| Missing or stale prerequisite | `unavailable` | `readiness` |
| PR-context failure | `error` | `pr_context` |
| Successful context without candidates | `ok` | `pr_context` |

4. Preserve available identity, changed files, gaps, and diagnostics.
5. Add `no_candidate_symbols` to the successful no-seed result without
   claiming that the PR has no impact.
6. Persist an early report when `output_dir` is safe. Return it in memory only
   when the output path itself is invalid.

Keep one report schema. Request and identity values may be `null` only when
they could not be established for a non-`ok` result. An `ok` result requires
the complete request and exact identity. Add `agent.invoked`; permit a null
model ID and region only when it is `false`.

### Output

A schema-valid `PRAnalysisReportV1` for every expected request outcome.

### Definition of done

- Invalid input never invokes PR context.
- Early outcomes never load settings, construct Strands, or call Bedrock.
- PR context runs at most once.
- Every early result validates against Pydantic and the checked-in schema.

## Slice 2: complete the bounded tools

### Input

A successful PR-context seed, its allowed candidate symbols, and the existing
symbol-impact adapter.

### Implementation

Extend the existing `EvidenceSession`; do not introduce a generic tool
framework.

For `symbol_impact`, enforce in host code:

```text
maximum calls       5
maximum rows        20
minimum offset      0
automatic paging    disabled
allowed targets     exact PR-context candidates
```

Every attempt consumes a slot. Keep raw impact XML in the evidence session;
return only normalized rows, gaps, metrics, evidence ID, and digest to the
model.

Add `ia_repomap_builder/pr_analysis_inspection.py` with one literal,
read-only tool:

```python
inspect_repository_evidence(
    terms: list[str],
    paths: list[str] | None = None,
) -> dict[str, Any]
```

Enforce:

```text
maximum calls                  2
maximum terms per call         8
maximum matches per term       20
maximum returned files         10
maximum relevant lines/file    200
maximum readable file          1 MiB
timeout                        30 seconds
```

Search Git-tracked files using fixed argument lists. Treat terms literally;
reject traversal, untracked paths, and symlinks escaping the checkout. Report
binary, oversized, capped, or timed-out searches explicitly. Sort terms,
files, and matches deterministically.

Initially authorize changed and impacted names, paths, and normalized evidence
identifiers. A second inspection may search a literal found in the first
bounded result. Return bounded excerpts only in memory and only when
`allow_source_inspection=True`; persist citations and match metadata instead.

### Output

At most seven model-callable attempts: five impact and two inspection calls.

### Definition of done

- Tool limits are host-enforced and rejected calls consume their allowance.
- Unauthorized targets cannot invoke Ripwire or broaden repository search.
- The model cannot run arbitrary shell, writes, Git network operations, or
  tests.
- Raw XML and source excerpts are absent from persisted tool evidence.
- Inspection is unavailable without explicit host opt-in.
- File and match caps, no-match results, and progressive literals discovered in
  bounded excerpts are disclosed or authorized deterministically.

## Slice 3: complete the coordinator boundary

### Input

A successful seed with candidates, explicit Bedrock settings, and one
invocation-scoped evidence session.

### Implementation

Keep orchestration in `ia_repomap_builder/pr_analysis.py`:

1. Register `pr-context-001` and its raw XML digest.
2. Build a sanitized seed containing exact identity, changed files,
   hunk-attributed symbols, callers, gaps, allowed symbol pairs, evidence
   references, and fixed limits.
3. Do not serialize `PrContextResult.as_dict()` into the prompt because it
   includes raw XML.
4. Construct one Bedrock model with temperature `0`, a 4,096-token response
   limit, the configured region and model, and the configured AWS profile when
   present.
5. Use Strands' sequential tool executor.
6. Require `AgentAnalysisDraftV1` structured output.
7. Validate every evidence reference and relationship distance.
8. Convert model, tool, or structured-output failures into a schema-valid
   `status="error", phase="analysis"` report.

The fixed prompt requires candidate-only conclusions, exact evidence IDs,
explicit gaps, lower-bound wording, and `execution_status="not_run"`. It must
not ask for hidden reasoning, severity, a merge verdict, or exhaustive impact.

After agent execution, re-read `HEAD` and working-tree state. Require the
original exact head and a clean tree. On mismatch, add
`repository_changed_during_analysis`, discard model-produced conclusions, and
do not publish an `ok` report.

### Output

A validated in-memory report bound to the same clean revision used by PR
context.

### Definition of done

- Raw XML is absent from the prompt.
- One agent is constructed at most once.
- Limits and sequencing do not depend on prompt compliance.
- Invalid model evidence cannot become a partial success.
- A changed head or dirty checkout cannot produce `status="ok"`.

## Slice 4: atomic external output

### Input

A validated report, retained XML evidence, sanitized inspection evidence, and
a safe external output directory.

### Implementation

Add `ia_repomap_builder/pr_analysis_output.py` with one internal writer:

```python
write_pr_analysis_bundle(
    output_dir: Path,
    report: PRAnalysisReportV1,
    evidence: Sequence[EvidencePayload],
) -> None
```

Write:

```text
output_dir/
  pr-analysis.json
  pr-analysis.md
  evidence/
    pr-context.xml
    symbol-impact-NNN.xml
    inspection-NNN.json
```

Revalidate the destination, reject a non-empty directory, stage the entire
bundle in a sibling temporary directory, write deterministic JSON and
Markdown, retain XML byte-for-byte, verify evidence digests, and atomically
rename the completed directory into place. Remove staging data after failure.

Render Markdown only from the validated JSON report. A persistence failure
returns an in-memory `status="error", phase="persistence"` result and leaves no
apparently complete bundle.

### Output

A complete, non-overwriting external bundle or no final bundle.

### Definition of done

- JSON, Markdown, and evidence appear together.
- Raw XML matches its recorded SHA-256 digest.
- Existing reports are never overwritten.
- Simulated failures leave no usable partial directory.
- No target-repository file is created or modified.

## Slice 5: tests and documentation

### Focused fake tests

Extend `tests/test_pr_analysis.py` for:

- valid and invalid coordinator requests;
- schema-valid invalid, unavailable, error, and no-seed reports;
- nullable failure identity and complete successful identity;
- `agent.invoked` invariants;
- no agent creation on early paths;
- one PR-context invocation;
- impact limits and rejected-call accounting;
- sanitized prompts and sequential execution;
- Bedrock temperature and token settings;
- invalid structured output or evidence references;
- post-run head and dirty-tree failures.

Add `tests/test_pr_analysis_inspection.py` for literal search, deterministic
ordering, authorization, traversal and symlink rejection, untracked, binary,
oversized and timeout cases, all limits, opt-in behavior, and excerpt-free
persisted evidence.

Add `tests/test_pr_analysis_output.py` for deterministic JSON and Markdown,
byte-identical XML, digest verification, external paths, no overwrite, new and
empty destinations, and atomic failure cleanup.

No default test may require AWS, a live `ia-app`, or a Ripwire execution.

### Documentation

Update:

- [`strands-pr-analysis-coordinator.md`](strands-pr-analysis-coordinator.md)
- [`ia-repomap-context-contract.md`](ia-repomap-context-contract.md)
- [`llm-agent-pr-blast-radius-research.md`](llm-agent-pr-blast-radius-research.md)
- `ia_repomap_builder/README.md`

Record the implemented request, early returns, fixed limits, skill boundary,
inspection opt-in, post-run guard, external output, and remaining deferred
work. Export `PRAnalysisRequestV1`, `PRAnalysisReportV1`, and
`run_pr_analysis` from `ia_repomap_builder/__init__.py`.

## Files touched

```text
ia_repomap_builder/pr_analysis.py
ia_repomap_builder/pr_analysis_inspection.py
ia_repomap_builder/pr_analysis_output.py
ia_repomap_builder/__init__.py
schemas/ia-repomap.pr-analysis-v1.schema.json
tests/test_pr_analysis.py
tests/test_pr_analysis_inspection.py
tests/test_pr_analysis_output.py
ia_repomap_builder/README.md
docs/design/strands-pr-analysis-coordinator.md
docs/design/ia-repomap-context-contract.md
docs/design/llm-agent-pr-blast-radius-research.md
```

Do not modify the Ripwire checkout, target `ia-app`, repository-map caches, or
the public module CLI.

The plan file itself is an intended tracked documentation change:

```text
docs/design/strands-pr-analysis-coordinator-implementation-plan.md
```

Its presence in the diff does not authorize runtime implementation outside the
files listed below.

## Validation

Run focused tests after each slice, then:

```shell
./.venv/bin/python -m unittest discover -s tests \
  -p 'test_pr_analysis.py'
./.venv/bin/python -m unittest discover -s tests \
  -p 'test_pr_analysis_inspection.py'
./.venv/bin/python -m unittest discover -s tests \
  -p 'test_pr_analysis_output.py'
./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
git diff --check
rg -n '^#{1,6} ' docs/
git status --short
```

A live Bedrock smoke test is optional, environment-gated, synthetic, and
requires explicit credentials, network access, model access, cost approval,
and source-inspection opt-in.

## Final acceptance criteria

- The documented request model is public and rejects unknown fields.
- Invalid, unavailable, error, and no-seed cases return schema-valid reports
  without invoking Bedrock.
- One successful request creates at most one agent.
- Impact and inspection limits cannot be bypassed.
- Raw XML remains canonical external evidence and never enters the prompt.
- Source excerpts are not persisted.
- Final `HEAD` and clean state match the pre-agent identity.
- Output publication is atomic, external, and non-overwriting.
- JSON and Markdown are deterministic host-generated artifacts.
- All existing and new lightweight tests pass.
- No deferred integration or target-repository change enters the slice.

## Trade-offs

- Focused inspection and output modules keep independent safety rules
  testable; the public coordinator remains in `pr_analysis.py`.
- Nullable failure metadata avoids invented identity values while conditional
  validation keeps successful results strict.
- Git-tracked literal search is narrower than arbitrary shell but excludes
  untracked and generated data by design.
- Bounded in-memory excerpts help verification while sanitized citations keep
  proprietary source out of persistent reports.
- Atomic bundle publication prevents partially usable reports at the cost of
  requiring staging and destination directories on the same filesystem.
- A fixed coordinator prompt is smaller and more deterministic than loading
  the broad Ripwire skill collection.

## Assumptions requiring external approval

- Enabling repository inspection requires product and security approval to
  send bounded tracked-source excerpts to the selected Bedrock model.
- A live Bedrock smoke requires AWS credentials, network access, approved
  model access, and cost approval; it is not part of deterministic completion.
- The report-schema adjustment assumes no external v1 consumer. If one exists,
  publish a product-approved v2 schema instead.
- Atomic directory publication requires the staging and final paths to share a
  filesystem.
