# PR Impact Step 0

Step 0 is the smallest reproducible input for PR impact analysis. It captures
the PR revision pair, the exact source change, initial affected surfaces,
related repositories, test obligations, review evidence, and unresolved
blockers.

Golden examples are:

- `examples/pr-impact/ia-app-pr-49156.yaml` — runtime parameter change.
- `examples/pr-impact/ia-app-pr-48706.yaml` — database migration and API change.

## Purpose

The fixture is used to:

- provide a stable input for the next impact-analysis steps;
- exercise agent and MCP response formats;
- compare generated findings with a human-reviewed baseline;
- preserve the source and review context behind each conclusion.

It is not a claim that every affected surface has already been proven. Each
fact must carry an explicit status such as `confirmed`, `assessed`,
`coverage_unknown`, `unavailable`, or `unresolved`.

## Required content

Every Step 0 fixture should contain:

1. PR identity, URL, base revision, and target revision.
2. Changed files and the smallest useful symbol, method, parameter, or field.
3. Initial affected surfaces: entities, APIs, UI, database, and permissions.
4. Related repositories and the declared relationship to the changed behavior.
5. Existing, recommended, and unresolved test obligations.
6. Automated and human review evidence, including the reviewed revision.
7. Confidence, risk, blockers, and unresolved questions.
8. Provenance describing the source and review snapshots used.

For migration PRs, Step 0 must additionally capture:

- DDL and DML migration files, including undo files;
- `dbschema.inc` table and field metadata;
- tables, fields, keys, indexes, constraints, triggers, and migration sources;
- old-to-new data movement, such as `MODULEPREF.property` to an
  `AIMLSETUP` column;
- runtime consumers of the changed table or fields;
- API entity/schema/path and declared permissions when present;
- migration-specific validation obligations and review findings.

## Evidence rules

- `confirmed` means directly supported by the source diff or authoritative
  metadata.
- `assessed` means a reasonable impact hypothesis that requires a later step
  to trace and validate.
- `coverage_unknown` means a repository is linked by manifest or known
  relationship, but test coverage has not been verified.
- `unavailable` means the required source or metadata was not available.
- `unresolved` means analysis is explicitly incomplete and must not be filled
  with a guess.
- Review comments are evidence about reviewer claims and process state. They
  are not automatically proof of runtime behavior.
- A review or CI result tied to a different commit must retain that commit
  identity and must not silently be presented as target-revision evidence.
- Database impact is a first-class surface. A change belongs in the database
  section only when the diff or later analysis provides evidence involving
  `app/source/common/dbschema.inc`, migrations, SQL, tables, fields, indexes,
  or database access paths. Runtime parameters are not database fields.
- A migration impact report must preserve differences between `dbschema.inc`
  metadata and executable DDL as unresolved evidence; it must not silently
  choose one representation as authoritative.
- A review comment tied to an older revision may inform an assessment, but it
  must be marked stale, addressed, or unresolved when the target revision is
  different.

## PR 48706 migration example

PR 48706 demonstrates the wider Step 0 shape. Its target revision changes 22
files across these surfaces:

- creates and populates the `AIMLSETUP` table;
- migrates AIML preferences from `MODULEPREF`;
- adds insert, update, and delete triggers and changes the legacy trigger;
- updates `app/source/common/dbschema.inc` and `aimlsetup.ent`;
- redirects AIML runtime consumers to the new table;
- adds a company-config GET/PATCH OpenAPI surface and permissions;
- records five inline Copilot findings, migration impact output, CI comments,
  Jira validation failure, and merge conflicts.

The fixture intentionally records both confirmed facts and unresolved checks.
For example, it captures a difference between the DDL composite primary key
`(cny#, record#)` and the `dbschema.inc` metadata, but does not declare which
one is correct.

## Gaps to assess

The current fixtures expose the following gaps. These are assessment items,
not reasons to expand Step 0 indiscriminately.

### Step 0 contract gaps

- Provenance lacks stable GitHub review/comment IDs, URLs, and timestamps for
  each summarized finding.
- Source evidence is uneven: database facts have field lists, while runtime
  changes need exact symbols, methods, and source lines.
- The status vocabulary is not yet a closed, documented set. Terms such as
  `review_required` and `stale_or_addressed_in_target` need defined semantics.
- CI and merge state are represented mainly through labels and comments rather
  than structured check-run, approval, and mergeability records.
- Raw review and issue-comment payloads are not retained; repeated automation
  comments are collapsed into material findings.
- Migration data movement is summarized but not yet modeled as a structured
  source-field to target-field mapping.

### Deferred follow-up gaps

- Exact test files, scenarios, and execution results are not resolved across
  `ia-test-automation`, `ia-restapi-automation-tests`, or `ia-gwdata-gl`.
- Review findings are not yet automatically checked against source semantics,
  such as boolean normalization or Liquibase conventions.
- Cross-repository relationships are candidate links until confirmed by the
  human-authored workspace manifest.
- Full call-graph, API-consumer, UI, permission-consumer, and database-consumer
  tracing remains outside Step 0.
- “No UI evidence” is not yet distinguished from “UI analysis was not run.”

The recommended next slice is to close provenance and source-location gaps,
then move to direct symbol, migration-consumer, API, and test tracing. Raw
comment archival, complete CI history, and automated review validation can be
added only if later workflows demonstrate that they are needed.

## Step 0 sign-off

Status: accepted as a valid, revision-pinned Step 0 evidence package, with
explicit warnings and follow-up work remaining.

Sign-off date: 2026-08-08.

Validation evidence:

- `./.venv/bin/python -m pytest tests/test_pr_impact_step0.py -q` — 19 tests
  passed.
- `./.venv/bin/python -m scripts.validate_pr_impact_step0 --fixture
  examples/pr-impact/ia-app-pr-48706.yaml --manifest
  config/workspace_repos.yaml --repo-key ia-main` — pass, exit code 0.
- `./.venv/bin/python -m scripts.validate_pr_impact_step0 --fixture
  examples/pr-impact/ia-app-pr-49156.yaml --manifest
  config/workspace_repos.yaml --repo-key ia-main` — pass, exit code 0.
- `git diff --check` — pass.

The validator confirmed the required fixture sections, full base and target
Git revisions, committed changed-path/status parity, evidence-path existence,
and evidence line bounds. The focused tests also cover added, modified,
deleted, and explicitly unsupported renamed paths, malformed diffs, unsafe or
duplicate paths, incomplete fixtures, warning-only findings, JSON output, and
CLI exit codes.

Accepted warnings are not validation failures. They preserve the following
known open items:

- unresolved database metadata and migration-analysis findings;
- unavailable UI or permissions evidence;
- unknown or unevidenced downstream test and gateway coverage;
- review records without stable external IDs or URLs;
- review evidence recorded against revisions older than the target revision.

This sign-off validates the Step 0 input package and its provenance boundary.
It does not sign off full call-graph, API, UI, database-consumer, permission,
runtime-correctness, or downstream test-coverage analysis. Those remain
follow-up steps.

## Deliberate Step 0 boundary

Step 1 receives the fixture's exact base and target revisions and validates the
Git diff before reading repo-v1 facts. The checkout is resolved from the
workspace manifest by `repo_key`; a user-supplied checkout-root argument is not
part of the interface. Git diff validation only; no catalog delta processing.
Step 1 is read-only, emits a separate JSON report, and does not modify this
YAML fixture.
Git diff validation only; no catalog delta processing.

Step 0 does not attempt to prove the full call graph, API contract, UI path,
database consumer path, repository test coverage, or permission behavior. Those
are follow-up steps. Its job is to establish a trustworthy, revision-pinned
starting point and make uncertainty visible.
