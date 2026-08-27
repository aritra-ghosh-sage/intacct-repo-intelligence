# Greenfield Intacct Code Intelligence Layer

## Document Status

Current Greenfield Strands architecture. The retained Step 1-8 artifacts are
compatibility and audit views inside the four-phase automated flow.

## Objective

Build an intelligence layer for the `intacct` GitHub organization that makes
cross-repository change impact visible and actionable.

When a pull request changes one repository, the system should identify with
revision-bound, inspectable evidence:

- The changed files and symbols
- The affected contracts, components, and interfaces
- Repositories that may be impacted
- Related open and recently merged pull requests
- Tests that cover the affected area
- Tests that are missing, stale, unavailable, or unresolved
- Recommended actions for the author and owners
- Whether a test update should be generated
- Whether a validated draft pull request can be opened in a test repository

Strands is the primary discovery and recommendation agent. It navigates
revision-bound repository handbooks and approved read-only tools to discover,
infer, rank, and explain impact. Live source and recorded tool results are the
factual substrate for its claims; deterministic boundaries validate identity,
revision, evidence shape, patch scope, validation, and writes.

## Problem Statement

The Intacct organization contains multiple repositories that participate in the
same product behavior. A change is usually raised in one repository while the
tests, fixtures, clients, schemas, or integration checks that protect that
behavior may live elsewhere.

The repositories currently do not share a deterministic view of:

- Cross-repository dependencies
- Interface ownership
- Test ownership
- Test-to-code coverage
- Related changes in flight
- Required follow-up changes after an interface change

This causes missed tests, duplicated investigation, late integration failures,
and uncertainty about who should act.

## Design Principles

### Evidence First

Every result must be backed by an immutable source reference such as:

- Repository and commit SHA
- Pull request number and head SHA
- Changed file and line or symbol
- Contract or component identifier
- Workflow run, job, check, or artifact identifier
- Test identifier and test source path

### Reproducible Evidence Boundaries

The same input revision and evidence snapshot must retain the same source and
tool evidence. Model ranking and semantic recommendations may vary and must
record the model, tool ledger, and evidence revision. Write eligibility rules
remain versioned and reproducible.

### Explicit Uncertainty

The system must distinguish `unknown`, `unavailable`, `candidate`, and `no
evidence`. It must not turn an empty result into a claim that no impact or test
exists.

### Precision Before Recall

An incorrect cross-repository relationship is more damaging than an omitted
candidate. Weak candidates may be shown as candidates, but must not be treated
as confirmed impact.

### Low Repository Friction

Repository onboarding should require central registration and, where possible,
one reusable CI workflow. A team should not need to build a custom parser or
maintain a bespoke service integration.

### Evidence-Backed Agent Discovery

Strands may discover, infer, and rank candidate relationships through approved
tools. `confirmed` and `strong_candidate` claims must cite exact source or tool
results. Repository eligibility, embeddings, names, and path proximity alone
remain candidate signals and cannot authorize a write.

## Target Architecture

```text
GitHub events and reconciliation
              |
              v
Immutable PR, commit, workflow, and artifact records
              |
              v
Repository handbooks, contracts, CI evidence, and read-only tools
              |
              v
Strands impact, coverage, and action analysis
              |
              v
Evidence-bound analysis report
              |
              +--> AI explanation and review summary
              |
              +--> Guarded test patch generation
                              |
                              v
                     Exact targeted validation
                              |
                              v
                    Draft test-repository PR
                              |
                              v
                  Human ready/merge decision
```

GitHub provides the primary ingestion primitives: pull request metadata and
changed files, workflow runs associated with a commit SHA, workflow artifacts,
and organization webhooks. See the [pull request REST API](https://docs.github.com/en/rest/pulls/pulls),
[workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs),
[workflow artifacts API](https://docs.github.com/en/rest/actions/artifacts),
and [organization webhooks API](https://docs.github.com/en/rest/orgs/webhooks).

## Five Architecture Options

### Option 1: Explicit Cross-Repository Contract Registry

Each repository declares a small machine-readable contract containing:

- Published APIs, schemas, events, packages, or database interfaces
- Consuming repositories
- Stable interface identifiers
- Test suites and test commands
- Ownership and escalation information
- Compatibility or version rules

The impact engine maps changed files to declared interfaces and then resolves
consumers and their declared tests.

Expected benefit:

- Lowest implementation complexity
- High explainability and auditability
- Works across languages and build systems
- Low repository onboarding cost
- Strong precision for known interfaces

Trade-offs:

- Relationships must be declared and maintained
- Undocumented dependencies may be missed
- Test discovery is limited by the quality of declarations
- Schema governance is required

This option is the simplest useful foundation, but it should not be the only
source of evidence.

### Option 2: CI Evidence Federation

Each repository publishes normalized CI evidence for every relevant commit:

- Commit SHA
- Workflow, job, and check IDs
- Tests discovered and executed
- Test IDs and test paths
- Build inputs and dependency versions
- Results and artifacts
- Optional coverage data

The intelligence layer stores the evidence centrally and resolves test and
repository relationships from observed CI behavior.

Expected benefit:

- Reuses existing repository build and test systems
- Avoids implementing a parser for every language initially
- Captures generated code and integration tests
- Supports low-friction adoption through reusable workflows

GitHub supports reusable workflows so the organization can centrally maintain
the evidence-publishing behavior. See [reusable workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

Trade-offs:

- Evidence quality depends on CI instrumentation
- Tests that never run or emit metadata remain undiscovered
- Artifacts may expire and must be copied to durable storage
- Different test frameworks require normalization
- Historical evidence can become stale after test or build changes

### Option 3: Runtime Coverage-Based Test Impact Analysis

The system records mappings such as:

```text
test case -> executed code or service -> repository/file/symbol
```

When a PR changes a file or symbol, the engine selects tests with recorded
execution coverage over that area.

CI/CD telemetry can use common semantics such as those defined by
[OpenTelemetry CI/CD conventions](https://opentelemetry.io/docs/specs/semconv/cicd/).

Expected benefit:

- Strong evidence for which tests actually execute changed code
- Finds tests in repositories that are not explicitly documented
- Useful for critical paths and integration behavior
- More precise than filename or directory matching

Trade-offs:

- Coverage is incomplete and environment-dependent
- Rare branches may not be observed
- Instrumentation adds CI cost and runtime
- Test and symbol identities can change
- Uncovered code does not prove that a test is irrelevant

### Option 4: Static Semantic Code Index

The intelligence layer indexes definitions, references, calls, implementations,
imports, API routes, schemas, and test-to-production relationships.

SCIP provides a language-agnostic protocol for source-code indexing. See
[SCIP](https://scip-code.org/) and the [SCIP reference](https://github.com/scip-code/scip/blob/main/docs/scip.md).

CodeQL is another possible implementation for supported languages because it
represents source code as queryable relational data. See [About CodeQL](https://codeql.github.com/docs/codeql-overview/about-codeql/).

Expected benefit:

- Strong source-level impact analysis
- Finds undocumented callers and test references
- Supports navigation and architecture use cases beyond PR review
- Enables repeatable relationship queries for supported languages

Trade-offs:

- High implementation and maintenance cost
- Language and build-context support varies
- Dynamic dispatch, reflection, and runtime configuration remain difficult
- Indexing every repository and revision is expensive
- Cross-repository symbol identity becomes a significant problem

### Option 5: Virtual Multi-Repository Integration Builds

When a PR changes repository A, the system checks out the source PR and
compatible target commits from dependent repositories, assembles a temporary
integration workspace, and runs affected tests.

Build systems such as Bazel demonstrate the value of deterministic dependency
and reverse-dependency queries. See [Bazel query](https://bazel.build/versions/6.6.0/query/quickstart?authuser=108).

Expected benefit:

- Highest confidence because dependent tests execute against the change
- Detects failures that static relationships cannot predict
- Directly validates repository compatibility

Trade-offs:

- Highest operational complexity
- Requires reproducible builds and compatible environments
- Expensive in compute and CI time
- Dependency version resolution is difficult
- Repository-specific build adapters may be required
- One unstable repository can affect the entire validation flow

This is best reserved for high-risk boundaries after the evidence platform is
established.

## Comparison

| Option | Deterministic strength | Test discovery | PR discovery | Onboarding | Operating complexity |
| --- | --- | --- | --- | --- | --- |
| Contract registry | High | Declared tests | Contract and interface keys | Low | Low |
| CI evidence federation | High | Observed CI tests | Commit, workflow, and historical evidence | Low to medium | Medium |
| Runtime coverage | High for observed behavior | Executed tests | Coverage-linked PRs | Medium | Medium |
| Static semantic index | High for supported languages | Source-linked tests | Symbol and dependency queries | Medium to high | High |
| Integration builds | Very high | Executed dependent tests | Dependency/build matrix | High | Very high |

## Recommended Approach

Build a **contract-and-evidence federation layer** first:

1. Central GitHub App or organization webhook
2. Minimal repository registration
3. Optional small contract file for external interfaces
4. Reusable CI evidence workflow
5. Deterministic impact and coverage rules
6. AI explanation and action generation
7. Guarded test patch generation and draft PR creation

This provides useful cross-repository intelligence without requiring every
repository to use the same language, build system, or source indexer. It also
creates a staged path to add runtime coverage, semantic indexing, or integration
builds only where the evidence gap justifies the cost.

## `ia-main` Business Contract Spine

The first semantic-index slice is intentionally bounded. It builds a
revision-pinned, read-only JSON sidecar from committed Git blobs; it does not
write to or replace `catalog/catalog.db`.

The canonical typed chain is:

```text
PHP symbol -> .ent entity -> API object/schema/workflow -> UI/import surface
```

The `.ent` parser is domain-specific and extracts literal entity declarations,
metadata, schema field mappings, nested literal field/table facts, entity
relationships, includes, and parser diagnostics. Generic PHP symbol extraction
remains separate from `.ent` semantics; dynamic or non-literal values remain
unresolved.

The strongest cross-layer bridge is the exact source mapping:

```text
objects.<module>.<object>.s<version>.schema.yaml
  -> x-mappedTo literal
  -> unique committed .ent stem
```

ActionUI `<entity>` references, NextGen object keys, explicit PHP manager/entity
arguments, CSV importer metadata, and bounded flat-file object configuration
are modeled as typed evidence. Generic XML, dynamic PHP, remote/generated
configuration, and runtime execution remain explicit gaps.

Resolution states include `explicit_source`, `resolved_exact`,
`framework_convention`, `candidate_static`, `ambiguous`, `dynamic`,
`unresolved`, and `unavailable`. Names, basenames, directory proximity, and AI
similarity never create authoritative identity.

Static semantic evidence can support a Step 2 candidate when an active
cross-repository contract identifies the consumer. It cannot prove executed
tests, confirmed CI coverage, or runtime business impact.

## PR Impact Analysis Flow

The supported operator-facing flow is:

```text
Capture
  -> Analyze with Strands, repository handbooks, and read-only tools
  -> Propose and validate remediation
  -> Publish GitHub Check, PR comment, and optional draft PR
  -> Human decides ready-for-review or merge
```

The Step 1-8 sections below describe retained evidence and mutation contracts
inside those phases. They are not separate operator handoffs.

### Step 1: Capture the Source PR

Greenfield Step 1 is a repository-neutral evidence-capture boundary. It is not
the existing repo-v1 direct-tracing Step 1 and does not depend on SQLite or a
semantic sidecar. Its contract is documented in
[PR Impact Step 1](pr-impact-step-1-greenfield.md).

Persist:

- Source repository
- PR number
- Base SHA
- Head SHA
- Changed paths and statuses
- PR metadata and linked issues
- Relevant workflow and check results

All later analysis must reference this exact source revision. Workflow and
check evidence must bind to the source PR head SHA, while linked issues remain
context only.

### Step 2: Resolve Impact Candidates

Use a two-tier candidate funnel:

1. Deep-inspect explicit contract mappings first.
2. Cheaply screen every enabled `discovery_eligible` test repository.
3. Deep-inspect a screened repository only when Strands finds supporting
   handbook, source, contract, CI, or test evidence.

Eligibility alone is not impact evidence. Each ranked candidate records its
evidence state, exact citations, model identity, and tool ledger.

### Step 3: Produce the Blast-Radius Outcome

The outcome should include:

- Directly affected components
- Potentially affected repositories
- Affected interfaces and owners
- Impacted test suites
- Related open and merged PRs
- Confirmed versus candidate impact
- Evidence gaps and inaccessible sources

Recommended blast-radius levels:

- `local`: only the source repository is affected
- `boundary`: a declared external interface is affected
- `multi_repo`: one or more consumer repositories are affected
- `systemic`: shared infrastructure, schema, build, or deployment behavior is affected
- `unknown`: evidence is insufficient to classify safely

### Step 4: Map Test Coverage

Classify every relevant test as:

- `covered`
- `indirectly_covered`
- `candidate`
- `stale`
- `missing`
- `unavailable`
- `unknown`

The report should answer:

- Which tests cover the changed interface?
- Which tests cover the dependent behavior?
- Which tests are expected but unavailable?
- Which tests need fixture, assertion, schema, or setup changes?
- Which impacted behavior has no confirmed test evidence?

### Step 5: Recommend Actions

Strands recommends actions from the evidence-backed analysis. Deterministic code
validates the action schema and any later automation boundary. Examples:

- Run a named test suite in a named repository.
- Request review from the owner of an impacted interface.
- Update a specific fixture because a schema field changed.
- Add a missing integration test for a declared consumer.
- Open a draft test PR after a validated patch is available.
- Block automated propagation because the target repository or base SHA is unavailable.

Each action should contain an owner, target repository, evidence, and completion
condition.

### Step 6: Generate a Test Patch When Justified

Test-PR generation is allowed for `confirmed` or `strong_candidate` analysis
when the target repository, base SHA, bounded paths, supporting tool citations,
and validation profile are exact. Qualifying reasons include:

- A declared contract changed
- An API or schema changed
- A test references a renamed or removed symbol
- A fixture must change to match a new contract
- A required test category is missing
- A confirmed compatibility failure requires a test update

Patch generation options:

1. Deterministic templates
2. AST or structured transformations
3. Existing fixture-generation utilities
4. Strands-generated bounded edits grounded in inspected target files

Strands receives bounded read-only tools over the source diff, source SHA,
target test-repository SHA, relevant test files, contract changes, test
conventions, and allowed paths. New tests may initially be inserted into an
existing captured test file; adding new repository files requires a separate
mutation-policy extension.

### Step 7: Validate the Patch

Before creating a PR, require:

- Clean patch application to the target base SHA
- Approved file-path scope
- No unrelated changes
- Formatting and lint checks
- Compilation or type checks
- Targeted tests
- Relevant integration tests
- Regression checks for the existing test suite
- Diff-size and generated-file policy checks
- Reproducible patch or generation fingerprint

If validation fails, return an actionable failure report and do not create a PR.

### Step 8: Create a Draft Test PR

The implemented guarded contract, trust boundary, idempotent GitHub operation,
and local non-writing handoff are documented in
[Greenfield PR Impact Step 8](pr-impact-step-8-greenfield.md).

The draft PR must include:

- Link to the source PR
- Source and target commit SHAs
- Impacted contract or interface
- Reason the test update was required
- Tests added or changed
- Validation commands and results
- Remaining uncertainty
- Evidence links
- Whether the patch was template-generated or AI-proposed

GitHub supports creating a branch from a specific commit and creating a pull
request from that branch. See [create a Git reference](https://docs.github.com/en/rest/git/refs)
and [create a pull request](https://docs.github.com/en/rest/pulls/pulls).

The generated PR is always a draft. Owner approval is not required to create the
draft. A human owner of the test repository must approve it before it becomes
ready for review or merge.

### Publish The Outcome

`publication.json` is the canonical user-facing outcome. It drives one GitHub
Check for machine-readable status and one marker-bound, idempotently updated PR
comment for the human narrative. A dashboard is deferred; any future dashboard
must consume this same outcome rather than create another truth model.

## AI Responsibilities and Restrictions

AI may:

- Navigate repository handbooks and revision-bound source tools
- Discover and infer evidence-backed repository and test relationships
- Explain evidence paths in plain language
- Rank candidates and choose investigation order
- Suggest investigation steps
- Propose bounded existing-test updates and missing tests
- Draft the test PR title and description

AI may not:

- Assert confirmed or strong-candidate relationships without recorded evidence
- Treat embeddings or naming similarity as proof
- Decide that missing evidence means no impact
- Expand the patch beyond approved files
- Merge or approve its generated PR
- Replace deterministic validation

Allowed evidence states are `confirmed`, `strong_candidate`, `candidate`,
`unresolved`, `unavailable`, and `no_evidence`. Ranking is advisory and never
silently promotes an evidence state.

## Repository Onboarding Model

The minimum onboarding path should be:

1. Install the central GitHub App or include the repository in the organization webhook scope.
2. Register repository ownership and default branch.
3. Add one reusable CI evidence workflow when test evidence is not already discoverable.
4. Declare only externally consumed interfaces and test entry points.

Repositories should not need to implement custom parsers. Language-specific
indexing and runtime coverage should remain optional enhancements.

## Delivery Phases

### Phase 1: Deterministic Ingestion

- GitHub App or organization webhook
- PR, commit, file, workflow, check, and artifact ingestion
- Immutable identities and evidence references
- Reconciliation for missed events

### Phase 2: Contract and Test Evidence

- Repository registry
- Minimal contract schema
- Reusable CI evidence workflow
- Test inventory and result normalization
- Coverage classifications

### Phase 3: Impact and Action Reports

- Strands blast-radius analysis with recorded tool evidence
- Related PR discovery
- Test-gap analysis
- Owner and action recommendations
- Canonical GitHub Check and idempotent PR comment presentation

GitHub Apps can create check runs with annotations on a commit, which is useful
for presenting evidence-backed impact findings directly on the source PR. See
the [Checks API](https://docs.github.com/en/rest/checks/runs).

### Phase 4: Guarded Test-PR Generation

- Deterministic templates
- Bounded AI patch proposals
- Validation gates
- Draft PR creation
- Idempotency by source PR, source SHA, target repository, and patch fingerprint

### Phase 5: Selective Advanced Analysis

- Runtime coverage for critical paths
- SCIP or CodeQL for supported languages
- Virtual integration builds for high-risk interfaces

## Safety and Governance

- Generated test PRs are draft by default.
- No automatic merge is permitted.
- Cross-repository writes require a GitHub App with narrowly scoped permissions.
- Every generated change must identify its source PR and exact source SHA.
- Repeated analysis must be idempotent.
- Evidence retention must outlive GitHub artifact expiration where the evidence is needed for historical analysis.
- Failed validation produces a report, not a partially trusted PR.
- The system must expose evidence gaps rather than silently lowering confidence.

## Success Metrics

Measure the system by outcomes rather than model quality alone:

- Percentage of PRs with an evidence-backed impact report
- Percentage of cross-repository relationships with explicit evidence
- Precision of confirmed impacted-repository candidates
- Percentage of impacted PRs with identified tests
- Number of missed regressions caught before merge
- Number of generated test PRs accepted without substantial rework
- False-positive rate of generated test PRs
- Median onboarding effort per repository
- Time from source PR creation to impact report
- Time from confirmed impact to validated draft test PR

## Final Recommendation

Start with contract metadata plus CI evidence federation, then add AI-assisted
test-PR generation behind strict validation gates.

This is the best balance of determinism, usefulness, and adoption cost. It
provides immediate value from existing GitHub and CI data, does not require a
deep semantic layer for every repository, and still supports stronger analysis
later for areas where the initial evidence is insufficient.
