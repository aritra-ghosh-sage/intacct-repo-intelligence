# Workflow Design Analysis

> **Scope notice:** This is general/legacy catalog workflow documentation. It
> is not the repo-v1 execution or PR-impact acceptance contract. Repo-v1 uses
> committed Git snapshots, full SQLite candidates, and explicit provenance;
> it does not use this document's legacy refresh, delta, or Ladybug workflow.

## Current State

### Model Structure

The current workflow model in the `intacct-repo-intelligence` catalog represents a **flat, single-action workflow model**:

- **workflows table:** Contains 302 rows
- **workflow_steps table:** Contains 302 rows  
- **Ratio:** 1:1 (each workflow has exactly one step)

### Interpretation

Every workflow currently represents a **single atomic action** rather than a sequence of operations. Examples include:
- `submit` (workflow_id=907)
- `approve` (workflow_id=908)
- `post` (workflow_id=909)
- etc.

This design reflects the current implementation discovered in `scripts/build_workflows.py`, which extracts workflow definitions from YAML configurations and allowed operations handlers.

## Design Options

### Option A: Keep Flat Model (Recommended)

**Description:** Maintain the current 1:1 workflow:step structure but clarify semantics.

**Characteristics:**
- Each workflow represents one atomic action
- Minimal schema changes required
- Clear, simple mental model
- Aligns with current data discovery process

**Schema Adjustments:**
- Rename conceptually to reflect single-action semantics
- Consider renaming `workflow` → `operation` or `action` to reduce semantic confusion
- Update documentation and query patterns

**Ladybug Graph Modeling:**
```
(Entity) -[HAS_OPERATION]-> (Operation {name, type, source})
(Entity) -[HAS_ACTION]-> (Action {name, sequence_order, entity})
```

**Pros:**
- Low schema migration complexity
- Matches current data model exactly
- Easier to reason about for auditing and compliance
- Current extraction logic is already optimized for this

**Cons:**
- Terminology mismatch ("workflow" implies multi-step)
- Cannot represent orchestrated business processes
- Limited expressiveness for complex entity lifecycle modeling

### Option B: Introduce Composite Workflows (Future Enhancement)

**Description:** Support hierarchical workflows that group atomic actions into business processes.

**Characteristics:**
- Root-level workflows represent business processes (e.g., `bill_lifecycle`)
- Sub-workflows represent atomic actions (e.g., `submit → approve → post`)
- Requires a parent-child relationship in workflows table

**Schema Adjustments:**
- Add `parent_workflow_id` column to workflows table
- Add `sequence_order` column to workflow_steps for deterministic ordering
- Create `workflow_hierarchy` table to track orchestration

**Ladybug Graph Modeling:**
```
(Entity) -[HAS_LIFECYCLE]-> (CompositeWorkflow {name, type})
(CompositeWorkflow) -[CONTAINS]-> (AtomicWorkflow {sequence_order})
(AtomicWorkflow) -[PERFORMS]-> (Action {name, type})
```

**Pros:**
- Expressive for complex entity lifecycle modeling
- Supports both atomic and composite workflows
- Enables workflow orchestration and sequencing
- Useful for compliance and audit trails

**Cons:**
- Significant schema migration required
- Requires new extraction logic in build_workflows.py
- More complex query patterns needed
- Unclear source evidence for multi-step orchestration in current codebase

## Decision

**DECISION: Option A (Flat Model) - ACCEPTED**

**Rationale:**
1. Current evidence from the codebase shows single-action workflows only
2. No source artifacts (YAML files, handlers) indicate multi-step orchestration
3. Follows "no fiction" constraint: don't invent relationships not present in evidence
4. Lower migration risk and implementation complexity
5. Future enhancement to Option B remains possible if evidence emerges

**Implementation Plan:**
- Keep current schema unchanged (no breaking migrations)
- Document this decision clearly in code and schema comments
- Update any external documentation referring to "workflows" to clarify single-action semantics
- Consider vocabulary refinement (workflow → operation) in future phases
- Establish process for identifying and handling multi-step orchestration if evidence emerges

## Source Evidence

- **Primary:** `scripts/build_workflows.py` extracts workflows from:
  - YAML configuration files (workflow definitions)
  - AllowedOperationsHandler classes (allowed operations per entity)
  
- **Secondary:** Entity mapping discovery uses workflow references primarily for:
  - Operation linking to entity
  - Action type classification (approval, posting, reverse, batch, item, ui, rest)

All current evidence points to single-action-per-workflow structure.

## Future Considerations

If multi-step orchestration evidence is discovered:
1. Files indicating workflow dependencies or ordering
2. SQL queries in .cqry files that reference workflow sequences
3. UI definitions showing multi-step forms or wizards
4. Service architecture documents describing business processes

Then Option B should be reconsidered with concrete source evidence.

## Source-of-Truth Boundary

OpenAPI linking is DB-first by design:

- Runtime linker source: `entity_nodes` + `openapispec_index`
- Each link_openapispec.py link run refreshes all openapispec_* mappings before linking, preventing stale mappings from surviving index changes.
- Deterministic priority: `x_mapped_to` match first, then heuristic name/module fallback
- `entity_definitions.jsonl` role: diagnostics/reconciliation only (not runtime linkage)

This keeps the linking path reproducible after scans/migrations and avoids coupling runtime
link decisions to generated JSONL artifacts.

## Refresh Boundary

This analysis is about workflow semantics, not the refresh pipeline. Catalog
refresh is now SQLite-first and may promote a new catalog generation before the
separate Ladybug projection is rebuilt. A successful SQLite refresh can leave
the graph intentionally stale until the operator runs the graph build step.

Workflow extraction itself still follows the same evidence rules and does not
depend on whether the refresh ran in full, auto, or delta mode.

### Contract-v3 execution boundary

Refresh planning compares committed revisions with the NUL-delimited raw Git
contract:

```bash
git diff --raw -z -M --no-abbrev <base-sha> <target-sha> --
```

Both rename paths, tree modes, blob IDs, and rename scores are preserved. Only
regular file blobs are accepted. Builders read a temporary target-commit
snapshot materialized by `git ls-tree` plus `git cat-file --batch`; checkout
bytes and Git filters are not evidence inputs.

Only scan, symbols, and relationships have exact delta execution. Workflow
extraction remains reset-style: a workflow source change, or invalidation from
its declared entity-root/OpenAPI inputs, rebuilds all workflows, nodes, edges,
and OpenAPI reference edges for that repository. Unrelated paths leave the
workflow stage skipped. Candidate-local parse and unresolved-source diagnostics
are converted to stable quality keys and never append to the standalone global
logs.

Repository prerequisites are explicit. An automation request builds `ia-main`
and then `ia-restapi-automation` in one candidate; a main request does not infer
the automation reverse dependent. If main REST endpoint evidence would be
rebuilt without automation in scope, planning fails before snapshot or candidate
creation.

Promotion requires verified sources, repository validation, SQLite integrity,
foreign keys, migration 025, semantic quality, restored manifest roots, a
logical fingerprint, final source-SHA checks, and parent CAS. The refresh lock
is held across preparation, no-op recording, failure recording, or promotion.
Ladybug construction and promotion are outside this workflow.

---

**Document Status:** Final Decision Recorded  
**Decision Maker:** Phase 2D Execution Agent  
**Last Updated:** 2026-07-31
**Approval Status:** Pending (awaiting stakeholder review)
