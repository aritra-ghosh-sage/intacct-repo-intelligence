# Greenfield PR Impact Step 8

Step 8 is the only Greenfield boundary allowed to create a test-repository
branch, commit, and pull request. It consumes the exact validated Step 3, Step
4, Step 6, and Step 7 artifacts, obtains a decision from an injected trusted
authorizer, re-verifies GitHub target state, and creates a draft PR. It never
approves, merges, marks ready for review, mutates the catalog, or changes the
source repository.

## Trust and eligibility

Step 7 remains deliberately non-PR-eligible. A Step 8 authorizer must
authenticate a decision bound to both the exact Step 7 report SHA-256 and its
validation fingerprint. The report must describe a production-eligible
`sandbox` runner. A local boolean, caller-provided JSON, content hash, or the
Step 7 `production_eligible` field alone cannot authorize writes.

The bundled authorizer always rejects. Production orchestration must inject an
implementation of `Step8Authorizer` and retain its authorization evidence. The
same orchestration injects a `GitHubWriter`; `GhApiWriter` is the provided
authenticated `gh api` transport.

Before authorization or GitHub access, Step 8 validates:

- strict Step 6 target evidence, both owner approvals, and `eligibility_profile: step7`;
- a successful, untampered Step 7 report with every validation category passed;
- Step 3/4/6/7 artifact fingerprint linkage;
- exact target repository, patch, generator, and path identity;
- a canonical GitHub source PR URL matching the source PR number; and
- the current v1 boundary: modified ordinary files only, no workflow files,
  and deterministic template generation only.

## GitHub operation

The deterministic branch is `strands/greenfield-<operation-id-prefix>`. After
authorization, Step 8:

1. verifies the target base branch still points to the validated target SHA;
2. reads the exact base commit and complete recursive tree;
3. matches each target-evidence blob ID and preserves its `100644` or `100755` mode;
4. creates base64 blobs, a tree over the exact base tree, and one single-parent
   commit whose fixed service identity and exact base-commit timestamp make the
   commit object deterministic across retries;
5. creates the branch ref without force-updating any existing ref; and
6. creates a same-repository pull request with `draft: true`.

The GitHub App or token needs narrowly scoped **Contents: write** and **Pull
requests: write** permissions. Step 8 has no calls for ref deletion, forced ref
updates, approvals, ready-for-review transitions, or merges.

## Idempotency and failure handling

The operation ID binds all upstream report fingerprints, Step 6's idempotency
key, target repository/base SHA, and base branch. It is embedded in the branch,
commit message, and PR body.

On retry, Step 8 searches all PR states for the exact deterministic head/base.
An exact draft is returned as `reused`. A matching branch without a PR is
verified by parent, operation marker, and full tree before only the PR is
created. Any collision or mismatched state blocks. Once any GitHub write request
is sent, its attempted stage is reported as `blobs`, `tree`, `commit`, `ref`, or
`pull_request`, even when the response is unavailable and the remote outcome is
unknown. Step 8 never attempts automatic cleanup because Git objects and refs
may need operator inspection.

Result statuses are:

- `created`: a new draft PR was created;
- `reused`: an exact existing draft was found without writes;
- `blocked`: a trust, identity, policy, or collision gate failed; and
- `failed`: GitHub was unavailable or rejected an operation.

Every successful result retains `human_owner_gate.status: pending`. Earlier
approvals authorize generation and validation only. A human owner of the test
repository controls the later ready-for-review decision.

## Local preparation CLI

The local command validates and renders the exact request and a blocked result.
It deliberately exits `1` and performs no GitHub calls because its authorizer
always rejects:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/prepare_greenfield_step8.py \
  --step3-report step3.report.json \
  --step4-report step4.report.json \
  --step6-report step6.report.json \
  --step7-report step7.report.json \
  --base-branch main \
  --request-output step8.request.json \
  --result-output step8.blocked.json
```

Exit `1` means the valid local handoff remains authorization-blocked. Exit `2`
means an input, contract, or rendering failure. A production service calls
`greenfield.step8_create.create_step8` with its trusted authorizer and GitHub
writer; no local CLI flag can upgrade the bundled rejecting authorizer.

`examples/greenfield/step8-artifact-shapes.example.json` documents the request,
authorization, and result envelopes. Placeholder values are illustrative and
are not production authorization evidence.

## PR body contract

The body deterministically contains the source PR and source/target commits,
impacted interface, deterministic reason and Step 5 action, changed tests and
fixtures, every Step 7 command/result/output fingerprint, Step 3/4 gaps and
unsupported coverage states, GitHub evidence links, non-link evidence
references, template provenance, and the pending human-owner gate. Rendering
escapes evidence-derived Markdown, uses variable-length delimiters for safe code
spans containing backticks, and fails if the body exceeds 60,000 bytes.
