# LLM Prompting Guide for Intacct Catalog MCP

## Overview

This guide is the accurate, concise usage reference for the Intacct catalog MCP server.

Principles:
- Use the MCP response envelope correctly.
- Chain tools from broad discovery to focused detail.
- Handle graph and ambiguity via status checks, not exceptions.

## Response Contract (Read This First)

All tools return a common envelope:

```json
{
  "contract_version": 1,
  "operation": "tool_name",
  "status": "ok|error|ambiguous|graph_unavailable|invalid_catalog",
  "data": {"...": "tool payload"},
  "snapshot": {"...": "catalog state"},
  "page": {"next_cursor": "...|null", "truncated": false},
  "error": {"code": "...", "message": "...", "details": {}}
}
```

Always read payload fields from data. For example, use response["data"]["workflows"], not response["workflows"].

Snapshot shape (response["snapshot"]):
- sqlite_snapshot
- graph_exists
- active_graph_build
- graph_fresh
- repositories

Page shape (response["page"]):
- next_cursor
- truncated

Recommended caller order:
1. Check status.
2. If status is error, read error.code and error.message.
3. If status is ambiguous, resolve with candidates and retry with repo_key or symbol_id.
4. Read payload from data.

## Tool Inventory

The server currently exposes 20 tools:
- catalog_search
- entity_context
- rest_coverage
- entity_test_coverage
- relationship_query
- workflow_structure
- entity_access_detail
- security_dependency_chain
- openapi_file_dependencies
- catalog_risk_summary
- risk_detail
- confidence_band_query
- api_surface
- workflow_context
- security_surface
- symbol_references
- file_impact
- provenance
- repository_list
- catalog_status

## Exact Tool I/O for LLMs

Use this section as the canonical call contract.

### Discovery and Context Tools

- catalog_search
    - input
        - required: query
        - optional: kind in all, entity, file, symbol, api, workflow, security
        - optional: limit, cursor, repo_key
    - data keys
        - repo_key
        - results: list of objects shaped as { kind, record }
    - notes
        - kind=all does not support cursor
        - kind=all may return status=error if combined results exceed limit

- entity_context
    - input
        - required: entity_name
        - optional: repo_key
    - data keys
        - entity
        - occurrences
        - mappings
        - roots
        - workflows
        - rest_endpoints

- workflow_context
    - input
        - required: entity_name
        - optional: workflow_type, repo_key
    - data keys
        - workflows

- workflow_structure
    - input
        - required: entity_name
        - optional: workflow_id, repo_key
    - data keys
        - workflows: list of objects shaped as { workflow, nodes, edges }

### API and Test Coverage Tools

- api_surface
    - input
        - at least one of: entity_name, path_fragment
        - optional: repo_key, limit, cursor
    - data keys
        - endpoints

- rest_coverage
    - input
        - required: entity_name
        - optional: version, limit
    - data keys
        - entity
        - endpoint_coverage
        - diagnostics
        - summary
        - coverage_scope
    - summary keys
        - endpoint_count
        - active_covered_endpoint_count
        - uncovered_endpoint_count
        - ci_conditional_only_endpoint_count
        - known_issue_only_endpoint_count
        - diagnostic_count

- entity_test_coverage
    - input
        - required: entity_name
        - optional: workflow_name, eligibility in active, known_issue, ci_only, conditional
        - optional: limit, cursor
    - data keys
        - entity
        - workflows
        - total_test_case_count
        - summary_by_eligibility
        - test_cases
        - filter
    - notes
        - workflow_name currently filters workflow context and requires at least one request step with operation_kind=workflow

### Dependency and Graph Tools

- relationship_query
    - input
        - required: name
        - optional: direction in outgoing, incoming
        - optional: resolution_classes, confidence_min, confidence_max, repo_key, limit, cursor
    - data keys
        - relationships

- symbol_references
    - input
        - provide symbol_id or symbol_name
        - optional: repo_key
    - data keys by status
        - ok: target, callers, referencers
        - ambiguous: candidates
        - graph_unavailable: target

- file_impact
    - input
        - required: file_path
        - optional: repo_key, depth 1..3, max_edges_per_symbol 1..1000
    - data keys by status
        - ok: file, seed_symbols, affected_symbols, traversal
        - ambiguous: candidates
        - graph_unavailable: file

### Security and Access Tools

- security_surface
    - input
        - required: key_fragment
        - optional: limit, cursor, repo_key
    - data keys
        - operations

- security_dependency_chain
    - input
        - required: op_key
        - optional: repo_key
    - data keys
        - chains: each item has operation, allowed_operations, policy_grants, menu_references

- entity_access_detail
    - input
        - required: entity_name
        - optional: surface_type, repo_key, limit, cursor
    - data keys
        - entity_name
        - links
    - notes
        - links do not include op_key; use record_id plus surface to join with other tools when needed

- openapi_file_dependencies
    - input
        - required: file_path
        - optional: repo_key
    - data keys by status
        - ok: file, outgoing_refs, incoming_refs
        - ambiguous: candidates

### Risk, Provenance, and Meta Tools

- catalog_risk_summary
    - input
        - none
    - data keys
        - relationships
        - entities
        - security
        - openapi
        - graph
        - repositories
        - risk_scores

- risk_detail
    - input
        - required: category in
            - low_confidence_relationships
            - unresolved_relationships
            - heuristic_relationships
            - entity_mapping_gaps
            - security_conflicts
            - security_unresolved_allowops
            - missing_file_ids_security
            - openapi_unknown_kind
        - optional: entity_name, symbol_name, repo_key, limit, cursor
    - data keys
        - category
        - records
    - notes
        - symbol_name is accepted by signature but not currently used by implementation

- confidence_band_query
    - input
        - required: category in relationships, entity_mappings, workflows, entity_roots
        - required: confidence_min, confidence_max
        - optional: repo_key, limit, cursor
    - data keys
        - category
        - confidence_range
        - records

- provenance
    - input
        - required: record_type in file, symbol, relationship, entity_mapping, workflow, rest_endpoint, security_operation
        - required: record_id
    - data keys
        - record_type
        - evidence

- repository_list
    - input
        - none
    - data keys
        - repositories

- catalog_status
    - input
        - none
    - data keys
        - counts

## Core Query Patterns

### 1. Discovery to Entity Context

```python
r1 = catalog_search(query="APBill", kind="entity", limit=10)
rows = r1["data"]["results"]

if not rows:
    # Fallback discovery
    r1 = catalog_search(query="APBill", kind="symbol", limit=10)

r2 = entity_context(entity_name="APBill")
if r2["status"] == "ok":
    entity = r2["data"]["entity"]
    mappings = r2["data"]["mappings"]
    roots = r2["data"]["roots"]
    workflows = r2["data"]["workflows"]
    endpoints = r2["data"]["rest_endpoints"]
```

### 2. Workflow Drill-Down

```python
r = workflow_context(entity_name="APBill")
for wf in r["data"]["workflows"][:3]:
    ws = workflow_structure(entity_name="APBill", workflow_id=wf["id"])
    for item in ws["data"]["workflows"]:
        node_count = len(item["nodes"])
        edge_count = len(item["edges"])
```

### 3. API Surface and Coverage

```python
api = api_surface(entity_name="Vendor", limit=25)
endpoints = api["data"]["endpoints"]

cov = rest_coverage(entity_name="Vendor", limit=25)
summary = cov["data"]["summary"]

# Available keys in summary:
# endpoint_count
# active_covered_endpoint_count
# uncovered_endpoint_count
# ci_conditional_only_endpoint_count
# known_issue_only_endpoint_count
# diagnostic_count
```

### 4. Dependency and Impact Analysis

```python
impact = file_impact(file_path="app/source/billing/processor.cls", depth=2)

if impact["status"] == "graph_unavailable":
    # SQLite fallback
    syms = catalog_search(query="processor", kind="symbol", limit=25)
    for item in syms["data"]["results"]:
        name = item["record"]["name"]
        relationship_query(name=name, direction="incoming", limit=25)
elif impact["status"] == "ok":
    seeds = impact["data"]["seed_symbols"]
    affected = impact["data"]["affected_symbols"]
    edges = impact["data"]["traversal"]["edges"]
```

### 5. Security Audit Chain

```python
ops = security_surface(key_fragment="GL.Post", limit=10)
for op in ops["data"]["operations"]:
    chain = security_dependency_chain(op_key=op["op_key"])
    for c in chain["data"]["chains"]:
        allowed = c["allowed_operations"]
        grants = c["policy_grants"]
        menus = c["menu_references"]

# Entity linkage by surface
links = entity_access_detail(
    entity_name="GLBatch", surface_type="security_operation", limit=25
)
surface_links = links["data"]["links"]
```

## Status Handling Patterns

### Graph-Dependent Tools

For file_impact and symbol_references:
- Check response["status"].
- If graph_unavailable, use relationship_query fallback paths.

### Ambiguity

Ambiguity is returned as status=ambiguous plus candidate records in data.

```python
r = file_impact(file_path="app/source/shared/file.cls")
if r["status"] == "ambiguous":
    for candidate in r["data"]["candidates"]:
        repo_key = candidate["repo_key"]
        file_impact(file_path="app/source/shared/file.cls", repo_key=repo_key)
```

### Not Found

Not-found cases are status=error with error.code values such as:
- entity_not_found
- symbol_not_found
- file_not_found
- operation_not_found

## Pagination Rules

- For paginated tools, follow page.next_cursor until null.
- Keep cursors scoped to the same tool and same query parameters.
- catalog_search with kind=all does not support cursor, and may return error if combined results exceed limit.

```python
cursor = None
all_cases = []
while True:
    resp = entity_test_coverage(entity_name="APBill", limit=25, cursor=cursor)
    all_cases.extend(resp["data"]["test_cases"])
    cursor = resp["page"]["next_cursor"]
    if not cursor:
        break
```

## Entity Test Coverage Notes

entity_test_coverage returns:
- data.entity
- data.workflows
- data.total_test_case_count
- data.summary_by_eligibility
- data.test_cases
- data.filter

Current workflow_name behavior:
- It filters the informational workflows list by name when workflows are available.
- It also requires test cases to include at least one request step with operation_kind set to workflow.
- It does not currently guarantee exact request-level matching to a specific workflow name.

Use workflow_context and workflow_structure for exact workflow reasoning.

## Entity Access Surfaces and Link Types

Typical surface values in entity_access_links include:
- workflow
- rest_endpoint
- dbschema_table
- security_operation
- security_resource
- security_policy
- security_menu_item
- security_menu

Common link_type values include:
- file_id_overlap
- entity_fk
- table_name_match
- security_key_match
- operation_policy_grant
- operation_menu_item
- operation_menu

## Anti-Patterns to Avoid

1. Reading payload fields outside data.
2. Treating graph_unavailable or ambiguous as exceptions.
3. Assuming catalog_search kind=all paginates.
4. Reusing a cursor across different tools or different query parameters.
5. Assuming rest_coverage returns endpoint_coverage_percent. It returns counts.
6. Calling entity_access_detail without entity_name.

## Quick Reference

| Goal | Primary Tool(s) | Fallback |
|------|-----------------|----------|
| Find entities/files/symbols | catalog_search | Repeat with different kind |
| Full entity context | entity_context | Combine catalog_search and workflow_context |
| Workflow list/detail | workflow_context, workflow_structure | entity_context.workflows |
| API and tests | api_surface, rest_coverage, entity_test_coverage | entity_context.rest_endpoints |
| Relationship dependencies | relationship_query | symbol_references when graph active |
| File blast radius | file_impact | catalog_search symbol + relationship_query incoming |
| Security dependency chain | security_surface, security_dependency_chain | entity_access_detail by security surface |
| Data quality and risk | catalog_risk_summary, risk_detail, confidence_band_query | repository_list and catalog_status |
| Provenance audit | provenance | entity_context and relationship_query evidence fields |

## Implementation References

- Server tools and contract: [server.py](../intacct_mcp/server.py)
- REST coverage fields: [rest_coverage.py](../catalog/rest_coverage.py)
- Entity access surfaces and link types: [build_entity_access_links.py](../scripts/build_entity_access_links.py)
- Schema: [schema.sql](../catalog/schema.sql)
