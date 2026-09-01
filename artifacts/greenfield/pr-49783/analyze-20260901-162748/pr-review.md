## 🔍 Review Summary

**Type:** Bug Fix | Feature | Refactor | Docs | Chore
**Scope:** [1-2 sentence description]
**Risk Level:** Low | Medium | High | Critical

**Evidence identity:** Source repository `[repo]` | Base `[sha]` | Head `[sha]`
**Assessment boundary:** Repositories assessed `[list]` | Owner evidence `[available/unavailable]` | CI execution `[status]`

---

## 📊 Changes at a Glance

- **Files:** X changed, Y additions, Z deletions
- **Commits:** N (avg. message quality: good/needs work)
- **Coverage:** API changes [Y/N] | DB migrations [Y/N] | UI [Y/N]

---

## 🧭 Blast Radius & Use-Case Flows

| Evidence class | Surface | Entity `.ent` file / flow | Status | Evidence |
|---------------|---------|---------------------------|--------|----------|
| Confirmed | [API/workflow/database/permission] | `path/to/entity.ent` or flow path | Confirmed | [catalog record/revision] |
| Candidate | Caller chain | `path/to/caller.cls` | Candidate | [relationship/revision] |

**Explicit gaps:**

- **Source identity:** `intacct/ia-app` base `88831b97966bf918cd08a882d2ac5d2aa4bf21a9`, head `071f786e237c0c92b385a19314f9a804c90ffedc`.
- **Assessed repositories:** intacct/ia-app.
- **CI execution:** execution_unavailable.
- **Ranked impact:** `ia-app` (candidate), `intacct/ia-gwdata-ap` (candidate), `intacct/ia-gwdata-contract` (candidate), `intacct/ia-gwdata-gl` (candidate), `intacct/ia-gwdata-project` (candidate), `intacct/ia-restapi-automation-tests` (candidate).
- **Coverage assessment:** not_assessed.
- **Planner lifecycle:** unavailable; 0 retained cycle(s).
- **Behavior projection:** revision-bound artifact retained.
**Affected behaviors and interfaces:**
- `behavior:0fb1098b6305204245d430f8` (partial): Builds department and location hierarchies and derives parent, child, display-name, and record-path information used by grouped report output. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:456b124545a723809a382731` (partial): Groups report transactions into monthly or custom accounting-period structures and recursively rewrites report arrays to contain period subtotals. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:57fe53265e84cb8a329dc6a3` (partial): Builds the GL report data XML by initializing reporting context, running pre-query and query processing, constructing report headers and transaction XML, and returning either inline XML or a temporary-file path. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:78a210c603a56f7c3729c160` (partial): Prepares GL report filters and accounting context, resolves period and account/location/department data, retrieves balances and transaction records, and prepares drill-down state for report generation. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:98e95371fbd924c066d129d7` (partial): Transforms GL and subledger query records into report transaction rows, including debit/credit values, document and drill-down data, dimensions, currencies, and subtransactions. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:ae03d1eb8cf41afac12a4c8e` (partial): Populates report header fields, reporting titles, dimension headers, transaction-state filters, consolidation metadata, and transaction drill-down operation metadata. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:dfb0f8b38f9e3f4651944fb2` (partial): Conditionally replaces eligible document-column values with record identifiers for accrual detailed reports and recursively applies the transformation to transaction and subtransaction rows. [source: `app/source/gl/GLLedgerReporter.cls`]
**Repository impact evidence:**
- `ia-app` (candidate): Step 3 impact outcome [evidence: `contract`]
- `intacct/ia-gwdata-ap` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-gwdata-contract` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-gwdata-gl` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-gwdata-project` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-restapi-automation-tests` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
**Coverage and obligations:**
- `behavior:0fb1098b6305204245d430f8` (candidate): `contract`
- `behavior:456b124545a723809a382731` (candidate): `contract`
- `behavior:57fe53265e84cb8a329dc6a3` (candidate): `contract`
- `behavior:78a210c603a56f7c3729c160` (candidate): `contract`
- `behavior:98e95371fbd924c066d129d7` (candidate): `contract`
- `behavior:ae03d1eb8cf41afac12a4c8e` (candidate): `contract`
- `behavior:dfb0f8b38f9e3f4651944fb2` (candidate): `contract`
**Executed CI evidence:**
- No exact CI execution evidence was supplied.
**Recommended actions:**
- `request_owner_review` in `ia-app` (candidate): impacted_interface_owner_available [evidence: `contract`]
- `request_owner_review` in `ia-app` (candidate): impacted_interface_owner_available [evidence: `contract`]
- `request_owner_review` in `ia-app` (candidate): impacted_interface_owner_available [evidence: `contract`]
- `request_owner_review` in `ia-app` (candidate): impacted_interface_owner_available [evidence: `contract`]
- `request_owner_review` in `ia-app` (candidate): impacted_interface_owner_available [evidence: `contract`]
- `request_owner_review` in `ia-app` (candidate): impacted_interface_owner_available [evidence: `contract`]
- `request_owner_review` in `ia-app` (candidate): impacted_interface_owner_available [evidence: `contract`]
- ci_artifact_unavailable:intacct/ia-gwdata-ap
- ci_artifact_unavailable:intacct/ia-gwdata-contract
- ci_artifact_unavailable:intacct/ia-gwdata-gl
- ci_artifact_unavailable:intacct/ia-gwdata-project
- ci_artifact_unavailable:intacct/ia-restapi-automation-tests
- ci_evidence_not_provided
- ci_linkage_unavailable:intacct/ia-gwdata-ap
- ci_linkage_unavailable:intacct/ia-gwdata-contract
- ci_linkage_unavailable:intacct/ia-gwdata-gl
- ci_linkage_unavailable:intacct/ia-gwdata-project
- ci_linkage_unavailable:intacct/ia-restapi-automation-tests
- cross_repository_discovery_requires_confirmed_relation_or_bound_ci_evidence
- intacct/ia-gwdata-ap:ci_linkage_unavailable:target_repository_has_no_source_revision
- intacct/ia-gwdata-ap:repos/intacct/ia-gwdata-ap/git/trees/3a412ac4d3792b49d9b13acfd27163e55baeae35?recursive=1: response_truncated
- intacct/ia-gwdata-contract:ci_linkage_unavailable:target_repository_has_no_source_revision
- intacct/ia-gwdata-contract:repos/intacct/ia-gwdata-contract/git/trees/9e23a432633c6b2a7d84390561a17a364662a823?recursive=1: response_truncated
- intacct/ia-gwdata-gl:ci_linkage_unavailable:target_repository_has_no_source_revision
- intacct/ia-gwdata-project:ci_linkage_unavailable:target_repository_has_no_source_revision
- intacct/ia-restapi-automation-tests:ci_linkage_unavailable:target_repository_has_no_source_revision
- related_pull_requests_not_modelled:revision_pinned_artifact_not_provided
- repository_inventory_not_provided
- semantic_index_not_provided:direct_semantic_components
- strands_planner_unavailable
- test_repository_not_assessed
- test_suites_unavailable:no_normalized_test_evidence
- workflow_has_no_test_execution:intacct/ia-gwdata-ap
- workflow_has_no_test_execution:intacct/ia-gwdata-contract
- workflow_has_no_test_execution:intacct/ia-gwdata-gl
- workflow_has_no_test_execution:intacct/ia-gwdata-project
- workflow_has_no_test_execution:intacct/ia-restapi-automation-tests
- workflow_metadata_only:intacct/ia-gwdata-ap
- workflow_metadata_only:intacct/ia-gwdata-contract
- workflow_metadata_only:intacct/ia-gwdata-gl
- workflow_metadata_only:intacct/ia-gwdata-project

- [Missing, unavailable, stale, ambiguous, not-modelled, or not-recorded-in-PR evidence]

---

## ✅ Reviewed

| File | Type | Status | Notes |
|------|------|--------|-------|
| `path/to/file1.cls` | Logic | ✓ | [brief comment] |
| `path/to/file2.js` | UI | ⚠ | [specific concern] |

---

## 🧪 Test Coverage & Obligations

| Test repository | Suite / scenario | Coverage status | Required action | Evidence |
|-----------------|------------------|-----------------|-----------------|----------|
| `repo/key` | `feature > scenario` | Confirmed / Candidate / Uncovered | Keep / Update / Add / Review | [revision and lines] |

**Coverage gaps:**

- [Exact missing, stale, unavailable, or weak coverage]
- [Use `not_assessed` when a nominated test repository lacks a confirmed relation and revision-bound test evidence]

---

## 🎯 Findings

### 🔴 Critical
- **[File:Line]** Exact issue with reproducible impact and fix

### 🟡 Medium Priority
- **[File:Line]** Pattern/inconsistency; recommend action

### 🟢 Nice-to-Have
- **[File:Line]** Suggestion for improvement

### ✅ Strengths
- **[File:Line]** What was done well

---

## 📋 Checklist

- [ ] All changed files reviewed
- [ ] No dead code or unused functions
- [ ] Consistency with existing patterns
- [ ] Documentation/comments adequate
- [ ] Tests cover new logic (if applicable)
- [ ] No obvious performance issues
- [ ] Follows team/language conventions

---

## 🎲 Confidence & Recommendation

**Confidence:** [score or `Not computed`; describe evidence scope, not business risk]
**Recommendation:** Approve ✓ / Request Changes ⚠ / Comment 💬

**Gaps/Assumptions:**
- [Explicit unresolved, unavailable, stale, or deferred evidence]
- [Evidence scope and target revision limitation]
- [AI guidance files are advisory context and never establish impact, ownership, or coverage]

**Next Reviewer:** @team-compliance (domain experts for e-invoicing logic)
