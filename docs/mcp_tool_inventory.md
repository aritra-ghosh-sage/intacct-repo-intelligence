# MCP Tool Inventory

Generated from the live FastMCP registration. Do not edit by hand.

Public tool count: **25**

## `api_registry`

Read exact V1, Beta, or V2i Registry evidence and Registry-local diagnostics without traversing OpenAPI-derived facts.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "file_path": {
      "anyOf": [
        {
          "description": "Exact repository-relative Registry source file path, required when operation='file'. Never pass an absolute filesystem path.",
          "examples": [
            "app/source/api/registries/RegistryV1.json"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "File Path"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "module": {
      "anyOf": [
        {
          "description": "Exact Registry module, required when operation='resource'. Use api_registry(operation='releases') to discover available releases.",
          "examples": [
            "accounts-payable"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Module"
    },
    "operation": {
      "description": "Registry evidence operation. releases summarizes exact Registry files; resource requires release, module, resource_kind, and resource_path; file requires file_path; issues returns Registry-local diagnostics.",
      "enum": [
        "releases",
        "resource",
        "file",
        "issues"
      ],
      "examples": [
        "resource"
      ],
      "title": "Operation",
      "type": "string"
    },
    "release": {
      "anyOf": [
        {
          "description": "Exact Registry release. Valid values are V1, Beta, and V2i.",
          "enum": [
            "V1",
            "Beta",
            "V2i"
          ],
          "examples": [
            "V1"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Release"
    },
    "repo_key": {
      "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
      "examples": [
        "ia-main",
        "ia-restapi-automation"
      ],
      "minLength": 1,
      "title": "Repo Key",
      "type": "string"
    },
    "resource_kind": {
      "anyOf": [
        {
          "description": "Exact Registry resource kind, required when operation='resource'.",
          "examples": [
            "objects"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Resource Kind"
    },
    "resource_path": {
      "anyOf": [
        {
          "description": "Exact Registry resource path, required when operation='resource'.",
          "examples": [
            "bill"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Resource Path"
    }
  },
  "required": [
    "operation",
    "repo_key"
  ],
  "title": "api_registryArguments",
  "type": "object"
}
```

## `api_surface`

Find REST endpoints by exact entity name, endpoint-path fragment, or both.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "entity_name": {
      "anyOf": [
        {
          "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
          "examples": [
            "APBill",
            "Customer",
            "GLAccount"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Entity Name"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "path_fragment": {
      "anyOf": [
        {
          "description": "Case-sensitive substring of a REST endpoint path. At least one of entity_name or path_fragment is required.",
          "examples": [
            "/objects/accounts-payable/bill"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Path Fragment"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "title": "api_surfaceArguments",
  "type": "object"
}
```

## `catalog_risk_summary`

Return aggregate catalog-quality signals and the category names accepted by risk_detail.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {},
  "title": "catalog_risk_summaryArguments",
  "type": "object"
}
```

## `catalog_search`

Discover exact catalog names, IDs, and repository-relative paths before calling narrower tools.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "kind": {
      "default": "all",
      "description": "Catalog record family to search. 'all' searches every family but does not accept cursor and errors if the combined result exceeds limit; choose one kind for pagination.",
      "enum": [
        "all",
        "entity",
        "file",
        "symbol",
        "api",
        "workflow",
        "security"
      ],
      "title": "Kind",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "query": {
      "description": "Case-insensitive name or path fragment. Use a specific kind when pagination may be needed.",
      "examples": [
        "ARInvoiceManager",
        "APBill"
      ],
      "minLength": 1,
      "title": "Query",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "query"
  ],
  "title": "catalog_searchArguments",
  "type": "object"
}
```

## `catalog_status`

Return high-level row counts for the active SQLite catalog.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {},
  "title": "catalog_statusArguments",
  "type": "object"
}
```

## `confidence_band_query`

Return records whose confidence or entity-root weight falls in an inclusive 0.0..1.0 band.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "category": {
      "description": "Record family whose confidence or weight is filtered.",
      "enum": [
        "relationships",
        "entity_mappings",
        "workflows",
        "entity_roots"
      ],
      "title": "Category",
      "type": "string"
    },
    "confidence_max": {
      "description": "Inclusive confidence score in the range 0.0..1.0.",
      "examples": [
        0.7
      ],
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Confidence Max",
      "type": "number"
    },
    "confidence_min": {
      "description": "Inclusive confidence score in the range 0.0..1.0.",
      "examples": [
        0.7
      ],
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Confidence Min",
      "type": "number"
    },
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "category",
    "confidence_min",
    "confidence_max"
  ],
  "title": "confidence_band_queryArguments",
  "type": "object"
}
```

## `entity_access_detail`

Return an entity's links to workflow, REST, security, and database surfaces with evidence IDs.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "entity_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Entity Name",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    },
    "surface_type": {
      "anyOf": [
        {
          "description": "Entity access surface type to include.",
          "enum": [
            "dbschema_table",
            "rest_endpoint",
            "security_menu",
            "security_menu_item",
            "security_operation",
            "security_policy",
            "security_resource",
            "workflow"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Surface Type"
    }
  },
  "required": [
    "entity_name"
  ],
  "title": "entity_access_detailArguments",
  "type": "object"
}
```

## `entity_context`

Return an entity's repository occurrences, code mappings, root symbols, workflows, and REST endpoints.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "entity_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Entity Name",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "entity_name"
  ],
  "title": "entity_contextArguments",
  "type": "object"
}
```

## `entity_test_coverage`

Return linked Gherkin scenarios, Jira references, eligibility, feature paths, lines, and ordered HTTP steps.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "eligibility": {
      "anyOf": [
        {
          "description": "Gherkin scenario eligibility classification.",
          "enum": [
            "active",
            "known_issue",
            "ci_only",
            "conditional"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Eligibility"
    },
    "entity_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Entity Name",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "workflow_action": {
      "anyOf": [
        {
          "description": "Exact, case-insensitive action from a canonical /workflows/<module>/<object>/<action> request. This fails closed when action evidence is stale.",
          "examples": [
            "approve"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Workflow Action"
    },
    "workflow_name": {
      "anyOf": [
        {
          "description": "Deprecated informational workflow-name filter, matched case-insensitively only in data.workflows. It never filters test scenarios; use workflow_action instead.",
          "examples": [
            "approve"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Workflow Name"
    }
  },
  "required": [
    "entity_name"
  ],
  "title": "entity_test_coverageArguments",
  "type": "object"
}
```

## `file_impact`

Traverse incoming graph references from every symbol in one exact repository-relative file.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "depth": {
      "default": 1,
      "description": "Incoming graph traversal depth from 1..3. Every depth requires a fresh active Ladybug graph.",
      "examples": [
        1
      ],
      "maximum": 3,
      "minimum": 1,
      "title": "Depth",
      "type": "integer"
    },
    "file_path": {
      "description": "Exact repository-relative path as stored by the catalog; do not pass an absolute filesystem path. Use catalog_search(kind='file') to discover paths.",
      "examples": [
        "app/source/apar/ARInvoiceManager.cls"
      ],
      "minLength": 1,
      "title": "File Path",
      "type": "string"
    },
    "max_edges_per_symbol": {
      "default": 25,
      "description": "Maximum incoming graph edges expanded per symbol at each depth; valid range is 1..1000.",
      "examples": [
        25
      ],
      "maximum": 1000,
      "minimum": 1,
      "title": "Max Edges Per Symbol",
      "type": "integer"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "file_path"
  ],
  "title": "file_impactArguments",
  "type": "object"
}
```

## `object_relationships`

Query provenance-backed ownership, hierarchy, visibility, and entity-context facts for one entity.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "axes": {
      "anyOf": [
        {
          "description": "Semantic axes to query; omit to query A, B, C, D, and E.",
          "items": {
            "description": "Semantic axis: A=ownership/composition, B=business hierarchy, C=location hierarchy, D=visibility/restriction, E=entity-context metadata.",
            "enum": [
              "A",
              "B",
              "C",
              "D",
              "E"
            ],
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Axes"
    },
    "confidence_min": {
      "anyOf": [
        {
          "description": "Inclusive confidence score in the range 0.0..1.0.",
          "examples": [
            0.7
          ],
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Confidence Min"
    },
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "depth": {
      "default": 1,
      "description": "Traversal depth from 1..3. Depth 1 uses SQLite evidence; depths 2..3 require a fresh Ladybug graph projection.",
      "examples": [
        1
      ],
      "maximum": 3,
      "minimum": 1,
      "title": "Depth",
      "type": "integer"
    },
    "direction": {
      "default": "both",
      "description": "Relationship direction relative to object_name.",
      "enum": [
        "incoming",
        "outgoing",
        "both"
      ],
      "title": "Direction",
      "type": "string"
    },
    "include": {
      "anyOf": [
        {
          "description": "Response sections to include; omit to include all five sections.",
          "items": {
            "enum": [
              "components",
              "relationships",
              "operations",
              "coverage",
              "conflicts"
            ],
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Include"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "object_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Object Name",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "object_name"
  ],
  "title": "object_relationshipsArguments",
  "type": "object"
}
```

## `openapi_file_dependencies`

Return incoming and outgoing OpenAPI reference edges for one exact catalog file.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "file_path": {
      "description": "Exact repository-relative path as stored by the catalog; do not pass an absolute filesystem path. Use catalog_search(kind='file') to discover paths.",
      "examples": [
        "app/source/apar/ARInvoiceManager.cls"
      ],
      "minLength": 1,
      "title": "File Path",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "file_path"
  ],
  "title": "openapi_file_dependenciesArguments",
  "type": "object"
}
```

## `provenance`

Resolve one catalog record ID to its repository revision and source evidence.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "record_id": {
      "description": "Exact catalog record ID returned by another tool.",
      "examples": [
        6361
      ],
      "minimum": 1,
      "title": "Record Id",
      "type": "integer"
    },
    "record_type": {
      "description": "Catalog table family that owns record_id. Use an ID returned by another catalog tool.",
      "enum": [
        "file",
        "symbol",
        "relationship",
        "entity_mapping",
        "workflow",
        "rest_endpoint",
        "security_operation"
      ],
      "title": "Record Type",
      "type": "string"
    }
  },
  "required": [
    "record_type",
    "record_id"
  ],
  "title": "provenanceArguments",
  "type": "object"
}
```

## `qa_impact`

Assess evidence-backed semantic, API, workflow, database, and test surfaces for changed files in one repository.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "$defs": {
    "QaChange": {
      "description": "One changed catalog file supplied to qa_impact.",
      "properties": {
        "file_path": {
          "description": "Exact repository-relative path as stored by the catalog; do not pass an absolute filesystem path. Use catalog_search(kind='file') to discover paths.",
          "examples": [
            "app/source/apar/ARInvoiceManager.cls"
          ],
          "minLength": 1,
          "title": "File Path",
          "type": "string"
        }
      },
      "required": [
        "file_path"
      ],
      "title": "QaChange",
      "type": "object"
    }
  },
  "properties": {
    "axes": {
      "anyOf": [
        {
          "description": "Semantic axes to assess; omit to assess all five axes.",
          "items": {
            "description": "Semantic axis: A=ownership/composition, B=business hierarchy, C=location hierarchy, D=visibility/restriction, E=entity-context metadata.",
            "enum": [
              "A",
              "B",
              "C",
              "D",
              "E"
            ],
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Axes"
    },
    "changes": {
      "description": "Changed files. Every item must contain file_path using the exact repository-relative catalog path.",
      "examples": [
        [
          {
            "file_path": "app/source/apar/ARInvoiceManager.cls"
          }
        ]
      ],
      "items": {
        "$ref": "#/$defs/QaChange"
      },
      "minItems": 1,
      "title": "Changes",
      "type": "array"
    },
    "depth": {
      "default": 1,
      "description": "Traversal depth from 1..3. Depth 1 uses SQLite evidence; depths 2..3 require a fresh Ladybug graph projection.",
      "examples": [
        1
      ],
      "maximum": 3,
      "minimum": 1,
      "title": "Depth",
      "type": "integer"
    },
    "include_tests": {
      "default": true,
      "description": "When true, include linked Gherkin tests and explicit test coverage gaps.",
      "title": "Include Tests",
      "type": "boolean"
    },
    "repo_key": {
      "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
      "examples": [
        "ia-main",
        "ia-restapi-automation"
      ],
      "minLength": 1,
      "title": "Repo Key",
      "type": "string"
    }
  },
  "required": [
    "changes",
    "repo_key"
  ],
  "title": "qa_impactArguments",
  "type": "object"
}
```

## `relationship_query`

Return direct extracted relationships for an exact source or target symbol name; call twice for both directions.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "confidence_max": {
      "anyOf": [
        {
          "description": "Inclusive confidence score in the range 0.0..1.0.",
          "examples": [
            0.7
          ],
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Confidence Max"
    },
    "confidence_min": {
      "anyOf": [
        {
          "description": "Inclusive confidence score in the range 0.0..1.0.",
          "examples": [
            0.7
          ],
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Confidence Min"
    },
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "direction": {
      "default": "outgoing",
      "description": "Direction relative to name; 'both' is not valid.",
      "enum": [
        "outgoing",
        "incoming"
      ],
      "title": "Direction",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "name": {
      "description": "Exact symbol name. It is matched against source_name for outgoing queries and target_name for incoming queries.",
      "examples": [
        "ARInvoiceManager"
      ],
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    },
    "resolution_classes": {
      "anyOf": [
        {
          "description": "Only return relationships in these resolution classes.",
          "items": {
            "description": "Relationship resolution classification to include.",
            "enum": [
              "builtin",
              "external",
              "heuristic",
              "project_resolved",
              "project_unresolved"
            ],
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Resolution Classes"
    }
  },
  "required": [
    "name"
  ],
  "title": "relationship_queryArguments",
  "type": "object"
}
```

## `repository_list`

Discover valid repo_key values and each repository's indexed branch, commit, and health status.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {},
  "title": "repository_listArguments",
  "type": "object"
}
```

## `rest_coverage`

Compare an entity's REST endpoints with linked Gherkin requests and report covered and uncovered endpoints.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "entity_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Entity Name",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "version": {
      "anyOf": [
        {
          "description": "Optional REST source version. Current catalog examples are 's1' and 's2'; omit to include all versions.",
          "examples": [
            "s1",
            "s2"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Version"
    }
  },
  "required": [
    "entity_name"
  ],
  "title": "rest_coverageArguments",
  "type": "object"
}
```

## `risk_detail`

Return paginated evidence rows for one category from catalog_risk_summary.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "category": {
      "description": "Catalog risk category returned by catalog_risk_summary.",
      "enum": [
        "low_confidence_relationships",
        "unresolved_relationships",
        "heuristic_relationships",
        "entity_mapping_gaps",
        "security_conflicts",
        "security_unresolved_allowops",
        "missing_file_ids_security",
        "openapi_unknown_kind"
      ],
      "title": "Category",
      "type": "string"
    },
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "entity_name": {
      "anyOf": [
        {
          "description": "Optional exact entity name, matched case-insensitively. Applied only when category='entity_mapping_gaps'.",
          "examples": [
            "APBill"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Entity Name"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    },
    "symbol_name": {
      "anyOf": [
        {
          "description": "Reserved symbol-name filter. The current implementation does not apply this filter.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Symbol Name"
    }
  },
  "required": [
    "category"
  ],
  "title": "risk_detailArguments",
  "type": "object"
}
```

## `security_dependency_chain`

Return an exact security operation with allowed operations, granting policies, and menu references.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "op_key": {
      "description": "Exact security operation key. Use security_surface to discover keys.",
      "examples": [
        "ee/lists/employee"
      ],
      "minLength": 1,
      "title": "Op Key",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "op_key"
  ],
  "title": "security_dependency_chainArguments",
  "type": "object"
}
```

## `security_surface`

Discover security operation IDs and exact keys by key or title fragment.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "key_fragment": {
      "description": "Case-insensitive substring matched against operation key and title.",
      "examples": [
        "employee"
      ],
      "minLength": 1,
      "title": "Key Fragment",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    }
  },
  "required": [
    "key_fragment"
  ],
  "title": "security_surfaceArguments",
  "type": "object"
}
```

## `symbol_references`

Find graph callers and referencers for one symbol; provide symbol_name or the preferred unambiguous symbol_id.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    },
    "symbol_id": {
      "anyOf": [
        {
          "description": "Exact symbol record ID from catalog_search or an ambiguous symbol_references response. Takes precedence over symbol_name.",
          "examples": [
            6361
          ],
          "minimum": 1,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Symbol Id"
    },
    "symbol_name": {
      "anyOf": [
        {
          "description": "Exact symbol name. If it resolves to multiple symbols, the response is ambiguous and returns candidates; retry with symbol_id.",
          "examples": [
            "create"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Symbol Name"
    }
  },
  "title": "symbol_referencesArguments",
  "type": "object"
}
```

## `ui_impact`

Return actionUI and NextGen screens linked by direct or supported UI roles.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "entity_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Entity Name",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "repo_key": {
      "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
      "examples": [
        "ia-main",
        "ia-restapi-automation"
      ],
      "minLength": 1,
      "title": "Repo Key",
      "type": "string"
    }
  },
  "required": [
    "entity_name",
    "repo_key"
  ],
  "title": "ui_impactArguments",
  "type": "object"
}
```

## `ui_surface_detail`

Return one paged evidence family for an exact actionUI or NextGen surface.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "cursor": {
      "anyOf": [
        {
          "description": "Opaque next_cursor returned in the previous response page. Do not construct or modify it.",
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Cursor"
    },
    "limit": {
      "default": 25,
      "description": "Maximum records to return for this page; valid range is 1..100.",
      "examples": [
        25
      ],
      "maximum": 100,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "record_kind": {
      "description": "Evidence family to return for the UI surface. Events include at most 100 nested handler-call records per event.",
      "enum": [
        "artifacts",
        "fields",
        "events",
        "scripts",
        "includes",
        "references",
        "issues"
      ],
      "examples": [
        "events"
      ],
      "title": "Record Kind",
      "type": "string"
    },
    "repo_key": {
      "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
      "examples": [
        "ia-main",
        "ia-restapi-automation"
      ],
      "minLength": 1,
      "title": "Repo Key",
      "type": "string"
    },
    "surface_key": {
      "description": "Exact UI surface key returned by ui_impact. actionUI keys begin with 'actionui:' and NextGen keys begin with 'nextgen:'.",
      "examples": [
        "actionui:app/source/gl/glbatch_form.xml",
        "nextgen:general-ledger/journal-entry"
      ],
      "minLength": 1,
      "title": "Surface Key",
      "type": "string"
    }
  },
  "required": [
    "surface_key",
    "repo_key",
    "record_kind"
  ],
  "title": "ui_surface_detailArguments",
  "type": "object"
}
```

## `workflow_context`

Return atomic workflow/action records for an exact entity, optionally filtered by workflow type.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "entity_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Entity Name",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    },
    "workflow_type": {
      "anyOf": [
        {
          "description": "Workflow classification to include.",
          "enum": [
            "allowed_operations",
            "approval",
            "posting",
            "reverse",
            "batch",
            "item",
            "entry",
            "ui",
            "rest"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Workflow Type"
    }
  },
  "required": [
    "entity_name"
  ],
  "title": "workflow_contextArguments",
  "type": "object"
}
```

## `workflow_structure`

Return workflow records plus their ordered nodes and edges for an entity.

Annotations:

```json
{
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false,
  "readOnlyHint": true,
  "title": null
}
```

Input schema:

```json
{
  "properties": {
    "entity_name": {
      "description": "Exact catalog entity name, matched case-insensitively. Use catalog_search(kind='entity') to discover names.",
      "examples": [
        "APBill",
        "Customer",
        "GLAccount"
      ],
      "minLength": 1,
      "title": "Entity Name",
      "type": "string"
    },
    "repo_key": {
      "anyOf": [
        {
          "description": "Catalog repository identifier. Current configured values are 'ia-main' and 'ia-restapi-automation'. Call repository_list to discover the values in the active catalog. Omit only when the requested name or path is unambiguous across repositories.",
          "examples": [
            "ia-main",
            "ia-restapi-automation"
          ],
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Repo Key"
    },
    "workflow_id": {
      "anyOf": [
        {
          "description": "Exact workflow record ID. Omit to return every workflow for the entity.",
          "examples": [
            907
          ],
          "minimum": 1,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Workflow Id"
    }
  },
  "required": [
    "entity_name"
  ],
  "title": "workflow_structureArguments",
  "type": "object"
}
```
