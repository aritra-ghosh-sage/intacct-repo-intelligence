# LLM Guidance for the Intacct Catalog MCP

> **Scope notice:** This guidance describes the general MCP/catalog query
> surface. It is not repo-v1 acceptance guidance and does not replace the
> read-only SQLite PR-impact contracts in
> [repo_v1_current_contract.md](design/repo_v1_current_contract.md).

Use [the generated MCP Tool Inventory](mcp_tool_inventory.md) as the
authoritative list of public names, annotations, descriptions, and input JSON
schemas. It is rendered from the live FastMCP registration and is checked in
CI; do not duplicate schemas here.

Every tool returns the same envelope:

```json
{
  "contract_version": 1,
  "operation": "tool_name",
  "status": "ok|error|ambiguous|graph_unavailable|invalid_catalog|capability_unavailable",
  "data": {"...": "tool payload"},
  "snapshot": {"...": "catalog state"},
  "page": {"next_cursor": "...|null", "truncated": false},
  "error": {"code": "...", "message": "...", "details": {}}
}
```

Read payload fields only from `data`. Check `status` before interpreting an
empty list: `ambiguous` requires a narrower retry, graph statuses require
SQLite-only handling, and `capability_unavailable` means the requested
evidence contract is not installed or is stale—not that there is no evidence.

Routing is intentionally simple: call `repository_list` and `catalog_status`
when freshness or repository scope matters; use `catalog_search` or the
relevant surface/discovery tool to find stable identifiers; then call the
focused detail tool. The narrow tools are not merged because their discovery
and detail responsibilities are different and a generic selector would make
calls less auditable.

For Gherkin evidence, `workflow_name` is deprecated and filters only the
informational `data.workflows` list. Use `workflow_action` to filter scenarios;
it matches case-insensitively only when the entity and action are evidenced by
the same canonical workflow request. A stale or legacy action build returns
`capability_unavailable` with `workflow_action_filter_unavailable`.
