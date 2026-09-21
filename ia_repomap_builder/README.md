# `ia_repomap`

This folder contains the local implementation of the Intacct repository-context
and self-hosted Ripwire PR-review contract. It compares a deterministic lexical
baseline with optional Aider RepoMap and Ripwire engines. The optional engines
are never silently substituted: an absent installation is returned as
`status: unavailable`.

The canonical repository-map contract, including configuration, artifact
identity, PR-context output, assumptions, and acceptance criteria, is in
[`docs/design/ia-repomap-context-contract.md`](../docs/design/ia-repomap-context-contract.md).
Its [execution status](../docs/design/ia-repomap-context-contract.md#execution-status)
records the completed implementation slices and their validation evidence.

## Input and output

```python
from pathlib import Path
from ia_repomap_builder import BuildRequest, build

result = build(BuildRequest(
    repo_root=Path("/path/to/ia-app"),
    scope=("app/source",),
    query="GLSetupManager preferences",
    engine="lexical",
    token_budget=4000,
))
print(result.as_dict())
```

The request is revision-aware and scope-relative. The result contains ranked
items, rendered context, diagnostics, runtime metrics, and a cache identity.
Generated context is intentionally not written into the repository.

## Repository configuration and readiness

The Ripwire review uses the configured repository profile for the supported
source scope. The profile is an input to readiness, not generated review
output. The self-hosted review command supplies the approved profile; users do
not need to copy internal setup files into the target repository.

```toml
schema_version = 1
engine = "ripwire"
scope = ["app/source"]
token_budget = 4000
php_family_extensions = [
  ".php", ".phtml", ".cls", ".ent", ".inc", ".cqry", ".rpt",
  ".menu", ".pol", ".wfl", ".shortcuts", ".qry", ".bin", ".map",
]
map_php_scope = ["app/source"]
```

The repository-map `token_budget` is a separate 4,000-token context budget.
The Strands model response ceiling is 2,048 tokens.

The self-hosted review has three operator prerequisites:

- `gh auth status` must show usable authentication for the canonical PR URL.
- `RIPWIRE_SKILLS_DIR` must point to an external directory containing the allowlisted files
  `ripwire-change-check/SKILL.md`, `ripwire-write-tests/SKILL.md`, and
  `ripwire-orient/SKILL.md`.
- Bedrock must use the standard boto3 credential chain (or optional
  `AWS_PROFILE`) plus `AWS_REGION` or `AWS_DEFAULT_REGION` and
  `BEDROCK_MODEL_ID`, supplied in the process environment or `.env.local`.

The three external skill files are an input allowlist, not the coordinator's
profile format. The host converts them to compact internal profiles named
`change_check`, `write_tests`, and `orient`; those profiles are prompt guidance
only and do not grant tools or permissions. Do not copy these files, a marker,
or an upstream skill directory into the target repository.

Before PR-context analysis, a clean exact-head checkout must have usable
external Ripwire state outside the repository. Missing or stale context is
reported as `unavailable`; the adapter does not silently substitute a different
revision or engine. The review command prepares this state automatically and
reuses it when the retained checkout is still exact and clean. Lower-level
module commands expose diagnostics for host-controlled use only.

The Python-only readiness and PR-context APIs are:

```python
from pathlib import Path
from ia_repomap_builder import (
    PrepareRepoMapRequest,
    PrContextRequest,
    build_pr_context,
    prepare_repomap,
)

repo = Path("/path/to/ia-app")
artifacts = Path("/safe/external/ia-repomap-artifacts")
prepare_repomap(PrepareRepoMapRequest(repo, artifacts))
result = build_pr_context(PrContextRequest(repo, artifacts, base_ref="origin/main"))
```

`prepare_repomap` requires the configured profile and a clean checkout. It
writes only external artifacts. `build_pr_context` requires usable prepared
state and returns `ok`, `unavailable`, or `error` without falling back to a
different revision.

Normalized changed-symbol seeds use `hunk-enclosing-v1`: Git's positive-side
hunks are attributed to spans inferred from Ripwire definition start lines.
The filtered definitions remain `candidate` evidence. Git hunk failures retain
file-wide candidates with an explicit gap; deletion-only changes, unattributed
hunks, and missing symbol lines are also disclosed rather than silently
treated as complete evidence. Canonical Ripwire XML is retained verbatim.

The adapter also exposes `direct-callers-v1` evidence for callers nested under
hunk-selected symbols. These one-hop relationships remain `candidate` evidence;
caller caps, malformed locations, and out-of-scope rows are reported as gaps.
Per-file aggregate impact paths and affected-test paths are normalized as
`candidate` context with explicit malformed, out-of-scope, and cap gaps.
Affected tests become deterministic report `test_areas` with
`execution_status="not_run"`; file-only impact does not mint symbol-level blast
radius rows. Owners and co-change data remain only in the canonical raw XML.

For an explicit next hop, the `symbol-impact` module command (or
`build_symbol_impact(PrImpactRequest(...))`) runs Ripwire's
`--impact=file:symbol` query against the same prepared external index. It
returns `symbol-impact-v1` candidate reachers with verbatim impact XML. Counts
are static-analysis floors; caps, pagination, ambiguous edges, unresolved
edges, importer rows, and malformed locations are reported as gaps. Impact
expansion is on demand and is not automatically run for every PR symbol.

## Strands PR-analysis coordinator

The local coordinator is available through the Python interface, not a public
CLI:

```python
from ia_repomap_builder import PRAnalysisRequestV1, run_pr_analysis

report = run_pr_analysis(PRAnalysisRequestV1.model_validate({
    "schema": "ia-repomap.pr-analysis-request/v1",
    "repo_root": "/path/to/ia-app",
    "base_ref": "origin/main",
    "artifact_root": "/safe/external/ia-repomap-artifacts",
    "output_dir": "/safe/external/ia-repomap-reports/run-001",
}))
```

The host validates the request and runs `build_pr_context()` once. If changed
files exist but no candidate symbols were returned, explicit Bedrock settings
permit one bounded Strands invocation in degraded file/diff mode; the report
includes `no_candidate_symbols`, uses `assessment="partial"`, and cannot
contain symbol-level blast-radius rows. If there are no changed files, there is
no seed and Strands is not constructed or invoked. With candidate symbols and
explicit Bedrock settings, one Strands agent may request up to five
candidate-scoped symbol-impact calls. Bounded repository inspection is a
separate, read-only tool and is available only when the host passes
`allow_source_inspection=True`; it permits at most two calls and retains only
sanitized citations in the external bundle.
Inspection reports disclose no-match results and omitted matching files or
matches as explicit gaps; literals visible in bounded excerpts may authorize a
single subsequent inspection query.

Reports are written atomically to the external `output_dir` as deterministic
`pr-analysis.json` and `pr-analysis.md`, with raw Ripwire XML retained under
`evidence/`. Inspection evidence stores citations and match metadata, not the
in-memory excerpts shown to the model. The writer refuses target-repository
paths, non-empty destinations, and prepared Ripwire cache directories. It
never prepares an index implicitly;
the caller must provide an exact clean checkout and matching external artifact.
Raw XML is canonical evidence, while all static relationships remain
candidate lower-bound evidence and test areas are `not_run`.

The coordinator loads only configured, allowlisted Ripwire skill profiles as
compact prompt guidance. Profiles do not grant tools or permissions, and the
upstream skill collection is not loaded wholesale. The current implementation
status is recorded in the canonical contract and
the [coordinator design](../docs/design/strands-pr-analysis-coordinator.md).
The local module interfaces support explicit preparation, readiness-gated
PR-context retrieval, on-demand symbol impact, and the bounded coordinator.
MCP, editor, harness, and GitHub publication integrations remain deferred. The
self-hosted `review` command is the supported local PR-review entry point
described below. This documentation pass does not claim a live target-repo,
GitHub, Ripwire, or Bedrock run.

## Running a self-hosted PR review

The review command resolves the PR through the authenticated GitHub CLI,
checks that `--repo` is the matching local repository, and creates or reuses a
clean detached PR worktree under an external workspace. It does not check out,
reset, merge, or edit files in the supplied `--repo` checkout. The detached
worktree is retained for later review reruns and inspection.

Authenticate before the first review:

```shell
gh auth status
```

To prepare or reuse the exact detached checkout and external Ripwire
prerequisites without running the coordinator, use the public `setup` command:

```shell
./.venv/bin/python -m ia_repomap_builder setup \
  https://github.com/owner/repository/pull/123 \
  --repo /path/to/local/repository \
  --workspace /safe/external/ia-repomap-review
```

`setup` resolves the PR revisions, prepares or reuses valid external state, and
reports the paths needed by a later `review` invocation. `review` performs the
same setup or reuse automatically before running the bounded coordinator.

If authentication is unavailable, run `gh auth login` according to the
organization's policy. Use a canonical GitHub pull-request URL and an existing
local checkout whose `origin` points to that PR's repository:

```shell
./.venv/bin/python -m ia_repomap_builder review \
  https://github.com/owner/repository/pull/123 \
  --repo /path/to/local/repository \
  --workspace /safe/external/ia-repomap-review
```

Add `--inspect` only when bounded, read-only source and test-area inspection is
wanted:

```shell
./.venv/bin/python -m ia_repomap_builder review \
  https://github.com/owner/repository/pull/123 \
  --repo /path/to/local/repository \
  --workspace /safe/external/ia-repomap-review \
  --inspect
```

`--repo` is the caller-owned source repository used for Git metadata and
retained worktrees; it is not replaced by the PR checkout. The command leaves
source files, the index, the current branch, `HEAD`, and the working tree
untouched. It does intentionally mutate Git metadata to fetch the required
objects, update `FETCH_HEAD`, and maintain the private
`refs/ia-repomap/pr/<number>/head` ref; it also registers the retained detached
worktree under the external workspace. `--workspace` must be external to the
supplied checkout and worktrees. If omitted, the command uses the local user
cache directory. Setup prepares the external Ripwire artifacts and then runs
the bounded coordinator automatically. A later invocation reuses the retained
detached worktree and external state when they remain valid; it does not
silently reuse dirty or mismatched state.

The command prints one JSON envelope and returns `0` for `ok`, `3` for
`unavailable`, and `2` for input or execution errors. The envelope includes
the resolved PR revisions, assessment, report directory, report files, and a
`remediation` list. Treat remediation as user-facing next actions rather than
as model commentary.

Typical remediation is to authenticate `gh`, correct the canonical PR URL,
point `--repo` at the matching readable checkout, choose a writable external
workspace, or repair the retained detached worktree named by the diagnostic.
The command never tells the model to repair repository state.

Each review writes outside the target repository:

```text
<workspace>/
  checkouts/       retained detached PR worktrees
  artifacts/       external Ripwire state
  reports/         JSON, Markdown, and retained evidence
```

The report contains `pr-analysis.json`, deterministic `pr-analysis.md`, and
external evidence files. Keep this workspace private when the source or
evidence is private; do not commit it to the target repository.

V1 does not execute tests, report executed coverage, modify the target
repository, publish a GitHub review, or use MCP. Test paths and behaviors are
candidate test areas with `execution_status="not_run"`; run and record tests
separately if execution evidence is required.

## Host-only PR-context diagnostics

The public self-hosted commands are `setup` and `review` above. The lower-level
`prepare`, `pr-context`, and `symbol-impact` commands below are diagnostic and
host-only interfaces; they are not a second operator setup path. Run them from
the builder repository root, or add this repository to `PYTHONPATH`. The host
supplies the bundled process-local marker while invoking the self-hosted flow.
Do not copy a marker or internal setup files into the target repository. The
target checkout is never inferred from a PR number; the host must select the
exact PR-head checkout and intended base ref or base SHA.

### 1. Verify the PR checkout

The checked-out `HEAD` in the target repository is treated as the PR head. It
must be clean because the prepared artifact identity is tied to the exact
revision.

```shell
git -C /path/to/ia-app rev-parse --show-toplevel
git -C /path/to/ia-app rev-parse HEAD
git -C /path/to/ia-app status --porcelain=v1
```

The status command must print no output.

### 2. Choose and pin the base

For local exploration, a locally available target branch such as `origin/main`
is acceptable. For repeatable CI or review automation, prefer the exact
40-character target-branch SHA from the PR event payload.

```shell
git -C /path/to/ia-app rev-parse --verify '<base-ref-or-sha>^{commit}'
git -C /path/to/ia-app merge-base HEAD '<base-ref-or-sha>'
```

The run records both the resolved `base_revision` and the computed
`merge_base`. Changed files and hunks are interpreted as `merge_base` to
`HEAD`, so a moving `origin/main` should be avoided when reproducibility
matters.

### 3. Prepare the external map artifact (host-only)

This diagnostic writes only under the caller-provided artifact root, outside
the target repository. The self-hosted `review` command performs it internally
with the bundled process-local marker.

```shell
./.venv/bin/python -m ia_repomap_builder prepare \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts
```

This requires the configured profile, the configured scope, and a supported
Ripwire binary. It is not a request to copy the profile or marker into the
target checkout.

### 4. Run PR context (host-only)

```shell
./.venv/bin/python -m ia_repomap_builder pr-context \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts \
  --base <base-ref-or-sha> \
  --output /safe/external/pr-context.json
```

`pr-context` checks readiness and never prepares an index implicitly. It emits
one `ia-repomap.command-result/v1` JSON envelope to stdout and, when `--output`
is supplied, writes the byte-identical envelope outside the target checkout.
Important fields are:

```text
status
result.status
result.identity.head
result.identity.base_revision
result.identity.merge_base
result.changed_files[].symbols
result.gaps
result.metrics.next_offset
```

If `result.gaps` contains `pagination`, rerun with the reported next offset
when you need the next changed-file window:

```shell
./.venv/bin/python -m ia_repomap_builder pr-context \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts \
  --base <base-ref-or-sha> \
  --offset <result.metrics.next_offset> \
  --output /safe/external/pr-context-next.json
```

If changed files are present but every `symbols` array is empty, the run found
file-level changes but did not produce symbol seeds. The coordinator may still
run bounded degraded file/diff analysis when Bedrock settings are supplied; it
must report `no_candidate_symbols` and `assessment="partial"`, with no
symbol-level blast-radius rows. Use the `hunk_symbol_unresolved`,
`hunk_no_head_lines`, truncation, and pagination gaps to explain the missing
seeds. With no changed files, the coordinator has no seed and does not invoke
Strands.

### 5. Run symbol impact for one PR-context symbol (host-only)

Use only the `path` and `name` from a symbol returned by `pr-context`. Impact
is on demand and is not automatically run for every changed symbol.

```shell
./.venv/bin/python -m ia_repomap_builder symbol-impact \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts \
  --symbol-path app/source/apar/CustomerPrintTemplateValidator.cls \
  --symbol-name buildTemplateFilters \
  --limit 20 \
  --offset 0 \
  --output /safe/external/symbol-impact.json
```

The result contains lower-bound transitive reachers, raw Ripwire impact XML,
metrics, identity, and explicit gaps for caps, pagination, ambiguity,
unresolved edges, importer rows, malformed locations, and out-of-scope rows.

### 6. Run the full Strands PR-analysis coordinator

The full coordinator is a Python API, not a public CLI. Bedrock settings are
required when candidate symbols are present. Set `AWS_REGION` or
`AWS_DEFAULT_REGION` and `BEDROCK_MODEL_ID` in the process environment or
`.env.local`; credentials come from the standard boto3 chain or optional
`AWS_PROFILE`, and are not request fields.

```python
from ia_repomap_builder import PRAnalysisRequestV1, run_pr_analysis
from ia_repomap_builder.pr_analysis import BedrockSettings

report = run_pr_analysis(
    PRAnalysisRequestV1.model_validate({
        "schema": "ia-repomap.pr-analysis-request/v1",
        "repo_root": "/path/to/ia-app",
        "base_ref": "<base-ref-or-sha>",
        "artifact_root": "/safe/external/ia-repomap-artifacts",
        "output_dir": "/safe/external/ia-repomap-reports/run-001",
    }),
    settings=BedrockSettings(
        region="us-east-1",
        model_id="<bedrock-model-or-inference-profile-id>",
    ),
    allow_source_inspection=False,
)
```

With candidate symbols and Bedrock settings, the host may invoke one Strands
agent. With changed files but no candidate symbols, the same settings permit a
bounded degraded file/diff invocation with no symbol-impact calls. The agent
can request up to five candidate-scoped `symbol-impact` calls in symbol-seeded
mode. If `allow_source_inspection=True`, it can also request up to two bounded,
read-only inspection calls. The host controls request validation, readiness,
tool limits, evidence validation, post-run repository checks, and persistence.

### 7. Read the outputs

The module commands emit command-result JSON envelopes. The full coordinator
writes an external report bundle:

```text
output_dir/
  pr-analysis.json
  pr-analysis.md
  evidence/
    pr-context.xml
    symbol-impact-001.xml
    inspection-001.json
```

`pr-analysis.json` is the authoritative `ia-repomap.pr-analysis/v1` report.
The Markdown file is rendered deterministically from that validated JSON. Key
fields are:

```text
status          ok | unavailable | error
assessment      complete | partial | unavailable | error
phase           request_validation | readiness | pr_context | analysis | persistence
request         repository, base, and analysis_schema
identity        repository, head, base, merge_base, configuration_digest, engine_identity
summary         purpose, behavioral_change, and confidence
changed_files   Git-confirmed changed files and candidate symbols
impacted_files  candidate impacted files and dependent-symbol counts
blast_radius    candidate lower-bound relationships
test_areas      suggested areas, always execution_status="not_run"
gaps            truncation, ambiguity, unresolved evidence, pagination, etc.
evidence        retained XML and inspection evidence with SHA-256 digests
diagnostics     host diagnostics
remediation     user-facing next actions
metrics         impact_calls, inspection_calls, and runtime details
agent.invoked   whether the Strands agent actually ran
```

Exit code `0` means `ok`, `3` means `unavailable` with remediation, and `2`
means invalid input or an execution error. `agent.invoked=false` means the
host produced a deterministic report without Strands. `agent.invoked=true`
means model and region provenance are recorded under `agent`.

For a participating Intacct repository, use the approved repository profile
through the self-hosted review setup. Generated maps and agent guidance stay
outside the target checkout as part of review setup.

## Optional engines

- `engine="aider"` requires a pinned Aider installation. Intacct PHP-family
  extensions are mapped only for the current process; this is an adaptation,
  not a claim about upstream Aider support.
- `engine="ripwire"` requires `RIPWIRE_BIN` or a `ripwire` executable on
  `PATH`. The binary must contain the Intacct extension aliases; the current
  `.ripwire_config` format cannot provide them. The adapter restricts Ripwire
  scopes to `app/source`, because the binary cannot exclude third-party `.map`
  files in a broader directory.

The release-pinned consolidated patch used by the evaluation is recorded at
`patches/ripwire-v0.4.0-intacct-repomap.patch`. It maps the configured
Intacct PHP-family suffixes to Ripwire's existing PHP grammar and adds the
bounded `--pr-history-commits` capability; it does not add a parser. `.map` is
included only under `app/source`; third-party source maps under `app/resources`
remain excluded. See
[`patches/README.md`](patches/README.md) for the full build-from-source,
patch-application, and CLI setup instructions.

The live `ia-app` smoke tests are deliberately opt-in. The repository scope
smoke requires `IA_APP_REPO`; the Ripwire scope smoke additionally requires
`RIPWIRE_BIN`; and the PR-context smoke requires all of
`IA_APP_REPO`, `IA_REPOMAP_ARTIFACT_ROOT`, `IA_APP_BASE_REF`, and
`RIPWIRE_BIN`. These checks are read-only and must not be used to commit
source excerpts or generated maps.

For the bakeoff, keep known-answer labels outside the source tree and run one
report per engine:

```python
from pathlib import Path
from ia_repomap_builder import evaluate, load_tasks

tasks = load_tasks(Path("/secure/ia-repomap-tasks.json"))
report = evaluate(Path("/path/to/ia-app"), tasks, "ripwire")
print(report["strict_file_recall_at_10"], report["mrr"])
```

Compare `lexical`, `aider`, and `ripwire` reports on the same task file. An
`unavailable` or `error` status is retained in the report and is not treated
as a successful zero-score result.

## Tests

```shell
./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```
