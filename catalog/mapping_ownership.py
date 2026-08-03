"""Ownership boundaries for materialized ``entity_mappings`` families."""

from __future__ import annotations

# These mappings are derived from entity_definitions.jsonl.  The entity builder
# may delete and recreate only these rows for a repository.
BUILD_ENTITIES_MAPPING_TYPES: tuple[str, ...] = (
    "manager",
    "editor",
    "lister",
    "picker",
    "allowed_operations_handler",
    "approval_manager",
    "reverse_manager",
    "item_manager",
    "batch_manager",
    "batch_picker",
    "form_editor",
    "entity_manager",
    "entry_manager",
    "pick_manager",
    "pick_picker",
    "workflow_schema_file",
    "workflow_history_file",
    "workflow_api_files",
    "yaml",
    "xslt",
    "inc",
    "xml",
    "sql",
    "rpt",
)

# These mappings are a projection of openapispec_index.  link_openapispec is
# their sole writer and may delete and recreate only these rows.
OPENAPI_MAPPING_TYPES: tuple[str, ...] = (
    "openapispec_schema",
    "openapispec_operations",
    "openapispec_history",
    "openapispec_view",
    "openapispec_uimeta",
    "openapispec_viewmeta",
    "openapispec_paths",
    "openapispec_actions",
    "openapispec_events",
    "openapispec_resource",
    "openapispec_components",
    "openapispec_security",
    "openapispec_unknown",
)


def placeholders(mapping_types: tuple[str, ...]) -> str:
    """Return a SQL placeholder list for a non-empty mapping type family."""
    if not mapping_types:
        raise ValueError("mapping type family must not be empty")
    return ", ".join("?" for _ in mapping_types)
