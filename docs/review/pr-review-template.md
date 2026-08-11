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

## ✅ Reviewed

| File | Type | Status | Notes |
|------|------|--------|-------|
| `path/to/file1.cls` | Logic | ✓ | [brief comment] |
| `path/to/file2.js` | UI | ⚠ | [specific concern] |

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

**Confidence:** 87/100
**Recommendation:** Approve ✓ / Request Changes ⚠ / Comment 💬

**Gaps/Assumptions:**
- Assumed DB value format is 'T'/'F' based on grep of related code
- Didn't trace all call sites for provider transitions

**Next Reviewer:** @team-compliance (domain experts for e-invoicing logic)

