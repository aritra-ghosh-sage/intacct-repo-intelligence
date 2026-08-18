## 🔍 Review Summary

**Type:** Bug Fix | Feature | Refactor | Docs | Chore
**Scope:** [1-2 sentence description]
**Risk Level:** Low | Medium | High | Critical

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

**Next Reviewer:** @team-compliance (domain experts for e-invoicing logic)
