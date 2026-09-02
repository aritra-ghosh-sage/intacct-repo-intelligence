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
- `behavior:05f71af8be6d4647568d98b3` (partial): Returns false unconditionally, indicating the GL ledger report does not support a matching-letter column. No calls made. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:202dbdf9ff662ed9b8b55f34` (partial): Builds select/from/where clause fragments for dimension display, including special-case handling for the employee dimension (joining contact/employeemst tables) and generic name/id column selection for other dimensions. Body is only partially shown (excerpt is cut off before completion at line 1236). Calls to the global helper isl_strpos() appear but its definition/target path is not present in the provided evidence, so no edge is emitted for it. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:2729cb7133b4dc75c18fed6d` (partial): Empty override hook for query-build customization prior to executing the report query; body contains only a comment and performs no calls. [source: `app/source/gl/GLLedgerReporter.cls`]
- `behavior:2ffa741f79174aa89766f6e8` (partial): Builds the SELECT clause fragment for user-chosen dimension columns based on the SHOWDIMENSIONVALUES parameter. Iterates configured dimensions and appends gl_info column references (optionally aliased) to the select string; no calls to other resolvable internal or external symbols are made within the shown hunk. [source: `app/source/gl/GLLedgerReporter.cls`]
**Repository impact evidence:**
- `ia-app` (candidate): Step 3 impact outcome [evidence: `contract`]
- `intacct/ia-gwdata-ap` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-gwdata-contract` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-gwdata-gl` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-gwdata-project` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
- `intacct/ia-restapi-automation-tests` (candidate): Step 3 impact outcome [evidence: `repository_inventory`]
**Coverage and obligations:**
- `behavior:05f71af8be6d4647568d98b3` (candidate): `contract`
- `behavior:202dbdf9ff662ed9b8b55f34` (candidate): `contract`
- `behavior:2729cb7133b4dc75c18fed6d` (candidate): `contract`
- `behavior:2ffa741f79174aa89766f6e8` (candidate): `contract`
**Executed CI evidence:**
- No exact CI execution evidence was supplied.
**Recommended actions:**
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
