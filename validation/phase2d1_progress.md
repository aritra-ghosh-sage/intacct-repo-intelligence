- issue_id: ISSUE-D1I
  status: resolved
  verification_output: |
    SELECT COUNT(*) FROM entity_mappings
    WHERE mapping_type = 'sql'
      AND source_text LIKE '%drop_all%';
    0

    SELECT source_text, COUNT(*)
    FROM entity_mappings
    WHERE mapping_type = 'sql'
    GROUP BY source_text
    ORDER BY 2 DESC
    LIMIT 10;
    (no rows)
  files_changed:
    - migrations/012_purge_sql_drop_mappings.sql
    - scripts/build_entities.py
    - project_inventory.json
    - validation/phase2d1_progress.md
  metrics_before:
    sql_mappings_total: 17
    sql_drop_all_mappings: 17
  metrics_after:
    sql_mappings_total: 0
    sql_drop_all_mappings: 0
  notes: Applied migration 012 to purge teardown/drop SQL noise and added SQL mapping opt-outs in build_entities (Option A).

- issue_id: ISSUE-D1G
  status: resolved
  verification_output: |
    SELECT kind, COUNT(*)
    FROM symbols s
    JOIN files f ON f.id = s.file_id
    WHERE f.path LIKE '%.xslt' OR f.path LIKE '%.xsl'
    GROUP BY kind;
    template|2453
    template_match|1652

    SELECT COUNT(*) FROM entity_mappings WHERE mapping_type='xslt';
    25

    SELECT COUNT(*) FROM entity_mappings
    WHERE mapping_type='xslt'
      AND (source_text IS NULL OR source_text='')
      AND file_id IS NULL;
    0
  files_changed:
    - scripts/build_entities.py
    - parser/extract_symbols.py
    - validation/validate_phase2d.py
    - validation/phase2d1_progress.md
  metrics_before:
    xslt_files_by_language: "xslt|301"
    xslt_mapping_count: 0
    xslt_symbol_count: 4101
  metrics_after:
    xslt_mapping_count: 25
    xslt_symbol_count: 4105
  notes: Confirmed extractor dispatch includes xslt, added xslt-only CLI extraction support, and added deterministic name-token based xslt mapping discovery in build_entities.

- issue_id: ISSUE-D1H
  status: resolved
  verification_output: |
    SELECT COUNT(*) FROM ui_companions;
    1659

    SELECT kind, COUNT(*)
    FROM ui_companions
    GROUP BY kind;
    editor|681
    lister|561
    picker|417

    SELECT COUNT(*) FROM ui_companions
    WHERE entity_id IS NULL OR kind IS NULL OR file_id IS NULL;
    0
  files_changed:
    - scripts/build_ui_companions.py
    - validation/validate_phase2d.py
    - validation/phase2d1_progress.md
  metrics_before:
    ui_companions_count: 0
  metrics_after:
    ui_companions_count: 1659
    entities_with_ui_companions: 940
  notes: Implemented idempotent table rebuild from entity_mappings via file_id join, including xslt kind inference from filename suffixes.

- issue_id: ISSUE-D1F
  status: resolved
  verification_output: |
    SELECT COUNT(*) FROM entity_mappings WHERE mapping_type='yaml';
    3

    SELECT en.name, em.source_text
    FROM entity_mappings em
    JOIN entity_nodes en ON en.id = em.entity_id
    WHERE em.mapping_type = 'yaml'
    LIMIT 20;
    Contact|app/tests/source/api/openapi/contact/__test.vendor.s1.api.yaml
    Company|app/tests/source/api/openapi/company/__test.userinfo.s1.api.yaml
    Document|app/tests/source/api/openapi/document/__test.sodocument.s1.api.yaml
  files_changed:
    - scripts/build_entities.py
    - validation/phase2d1_progress.md
  metrics_before:
    yaml_mappings: 0
    non_openapispec_yaml_files: 108
  metrics_after:
    yaml_mappings: 3
    yaml_mappings_missing_provenance: 0
  notes: Sampled 10 non-openapispec YAML files; most were email templates or test OpenAPI fixtures, and only entity-like filenames were mapped via filename/path-segment matching (no content parsing).

- issue_id: ISSUE-D1J
  status: resolved
  verification_output: |
    SELECT
      COUNT(*) AS total,
      COUNT(DISTINCT em.file_id) AS linked,
      ROUND(100.0 * COUNT(DISTINCT em.file_id) / COUNT(*), 2) AS pct
    FROM openapispec_index oi
    LEFT JOIN entity_mappings em
      ON em.file_id = oi.file_id
      AND em.mapping_type LIKE 'openapispec%';
    3853|1239|32.16
  files_changed:
    - scripts/link_openapispec.py
    - validation/validate_phase2d.py
    - validation/phase2d1_progress.md
  metrics_before:
    openapi_total_files: 3853
    openapi_linked_files: 568
    openapi_linkage_percent: 14.74
  metrics_after:
    openapi_total_files: 3853
    openapi_linked_files: 1239
    openapi_linkage_percent: 32.16
    openapi_threshold_percent: 30.0
  notes: Sampled 30 unlinked rows showed three dominant gaps (slug naming variants, missing resource-path extraction, and module-name mismatch). Added deterministic rules for canonical normalization, slug parsing, resource-path parsing, canonical suffix/prefix normalization, and module-family scoped matching. Threshold was set to 30% with justification because many OpenAPI rows represent workflow/view/meta descriptors without one-to-one entity names in entity_nodes.

- issue_id: ISSUE-D1K
  status: resolved
  verification_output: |
    python -c "
    import json
    gold = [json.loads(l) for l in open('validation/gold_entities_v2.jsonl')]
    sources = set(g['source'] for g in gold)
    print(f'Gold size: {len(gold)}')
    print(f'Distinct sources: {sources}')
    assert len(sources) >= 2, 'Gold set must draw from >=2 independent sources'
    "
    Gold size: 54
    Distinct sources: {'ent_filesystem', 'product_docs'}
  files_changed:
    - validation/gold_entities_v2.jsonl
    - validation/validate_phase2d.py
    - validation/phase2d1_report.md
    - validation/phase2d1_progress.md
  metrics_before:
    independent_gold_set_exists: false
  metrics_after:
    independent_gold_set_exists: true
    gold_size: 54
    distinct_sources: 2
    recall_percent: 3.70
    missing_count: 52
  notes: Built v2 gold set from independent filesystem .ent basenames and OpenAPI document references without filtering against entity_nodes; phase2d1_report now includes recall and explicit missing list.
