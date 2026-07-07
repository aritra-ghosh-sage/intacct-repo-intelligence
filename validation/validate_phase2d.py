#!/usr/bin/env python3
"""
Phase 2D validator - Comprehensive checks for Phase 2D data completeness.

Checks:
  - tables_exist: Confirm Phase 2D tables exist
  - declared_vs_actual_mapping_types: Compare declared vs actual sources
  - mapping_provenance: Verify mappings have source_text or file_id
  - workflow_step_ratio: Report workflow-to-step ratio
  - rest_endpoint_coverage: Report REST endpoint coverage
  - ui_companion_coverage: Report UI companion coverage
  - cqry_coverage: Confirm .cqry symbols and mappings exist

Output: validation/phase2d_report.md
Exit code: 0 if all assertions pass, non-zero if any fail
"""

import sqlite3
import sys
import json
from pathlib import Path
from datetime import datetime

def get_connection():
    db_path = Path(__file__).parent.parent / "catalog" / "catalog.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def check_tables_exist(conn):
    """Check if Phase 2D tables exist."""
    required_tables = {
        "rest_endpoints",
        "ui_companions",
        "repos",
        "services",
        "knowledge_items",
        "workflow_nodes",
        "workflow_edges",
        "openapispec_index",
    }
    
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    
    existing_tables = {row["name"] for row in rows}
    present = required_tables & existing_tables
    missing = required_tables - existing_tables
    
    return {
        "status": "PASS" if not missing else "FAIL",
        "present": sorted(present),
        "missing": sorted(missing),
        "count": len(present),
        "required": len(required_tables),
    }


def check_declared_vs_actual_mapping_types(conn):
    """Check declared sources of truth against actual mapping types."""
    # Declared sources from ISSUE-005
    declared_sources = {
        ".ent": "HIGH",
        ".cls": "HIGH",
        ".inc": "HIGH",
        ".cqry": "HIGH",
        ".yaml": "HIGH",
        ".sql": "MEDIUM",
        ".xslt": "MEDIUM",
        ".html": "MEDIUM",
        ".phtml": "MEDIUM",
    }
    
    # Map extensions to mapping_type values
    mapping_type_map = {
        ".ent": "manager",  # .ent files are typically managed entities
        ".cls": "editor",   # .cls files contain companion classes
        ".inc": "inc",
        ".cqry": "cqry",
        ".yaml": ["openapispec_schema", "openapispec_operations", "openapispec_history", "yaml"],
        ".sql": "sql",
        ".xslt": "xslt",
        ".html": "html",
        ".phtml": "phtml",
    }
    
    # Check which extensions have 0 files (out of scope)
    out_of_scope = set()
    for ext in declared_sources:
        file_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE path LIKE ?",
            (f"%.{ext.lstrip('.')}",)
        ).fetchone()["cnt"]
        if file_count == 0:
            out_of_scope.add(ext)
    
    # Get actual mapping types
    rows = conn.execute(
        "SELECT DISTINCT mapping_type FROM entity_mappings ORDER BY mapping_type"
    ).fetchall()
    actual_types = {row["mapping_type"] for row in rows}
    
    # Check each declared source
    results = {}
    for ext, authority in declared_sources.items():
        if ext in out_of_scope:
            results[ext] = {
                "authority": authority,
                "status": "OUT_OF_SCOPE",
                "reason": "0 files found in repository",
                "expected_types": [],
                "found": True,  # Mark as found since it's handled as out of scope
            }
        else:
            mapping_types = mapping_type_map.get(ext, [])
            if not isinstance(mapping_types, list):
                mapping_types = [mapping_types]
            
            found = any(mt in actual_types for mt in mapping_types)
            results[ext] = {
                "authority": authority,
                "status": None,
                "expected_types": mapping_types,
                "found": found,
            }
    
    # Only fail on HIGH/MEDIUM sources that are not out of scope and not found
    failures = {
        ext: v for ext, v in results.items() 
        if not v["found"] and v.get("status") != "OUT_OF_SCOPE"
    }
    
    return {
        "status": "PASS" if not failures else "FAIL",
        "results": results,
        "failures": failures,
    }


def check_mapping_provenance(conn):
    """Check that all mappings have source_text or file_id."""
    rows = conn.execute(
        """
        SELECT COUNT(*) as orphan_count FROM entity_mappings
        WHERE (source_text IS NULL OR source_text = '')
          AND file_id IS NULL
        """
    ).fetchall()
    
    orphan_count = rows[0]["orphan_count"] if rows else 0
    
    return {
        "status": "PASS" if orphan_count == 0 else "FAIL",
        "orphan_count": orphan_count,
        "total_mappings": conn.execute(
            "SELECT COUNT(*) as cnt FROM entity_mappings"
        ).fetchone()["cnt"],
    }


def check_workflow_step_ratio(conn):
    """Report the workflow-to-step ratio."""
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM workflows) AS workflows,
          (SELECT COUNT(*) FROM workflow_steps) AS steps,
          ROUND(
            1.0 * (SELECT COUNT(*) FROM workflow_steps) /
            NULLIF((SELECT COUNT(*) FROM workflows), 0),
            2
          ) AS avg_steps
        """
    ).fetchone()
    
    return {
        "status": "REPORT",
        "workflows": row["workflows"],
        "workflow_steps": row["steps"],
        "avg_steps_per_workflow": row["avg_steps"],
        "note": "This is a modeling decision (see ISSUE-004). No assertion threshold.",
    }


def check_rest_endpoint_coverage(conn):
    """Report percentage of entities with REST endpoints."""
    total_entities = conn.execute(
        "SELECT COUNT(*) as cnt FROM entity_nodes"
    ).fetchone()["cnt"]
    
    entities_with_endpoints = conn.execute(
        """
        SELECT COUNT(DISTINCT entity_id) as cnt FROM entity_mappings
        WHERE mapping_type = 'rest_endpoint'
        """
    ).fetchone()["cnt"]
    
    coverage = 100.0 * entities_with_endpoints / total_entities if total_entities > 0 else 0
    
    return {
        "status": "REPORT",
        "total_entities": total_entities,
        "entities_with_rest_endpoints": entities_with_endpoints,
        "coverage_percent": round(coverage, 2),
        "note": "No threshold set. Reported for awareness.",
    }


def check_ui_companion_coverage(conn):
    """Report percentage of entities with UI companions."""
    total_entities = conn.execute(
        "SELECT COUNT(*) as cnt FROM entity_nodes"
    ).fetchone()["cnt"]
    
    # UI companions include: ui_companion, form_editor, picker, lister, etc.
    ui_mapping_types = ["ui_companion", "form_editor", "picker", "lister", "editor"]
    
    entities_with_ui = conn.execute(
        f"""
        SELECT COUNT(DISTINCT entity_id) as cnt FROM entity_mappings
        WHERE mapping_type IN ({','.join(['?' for _ in ui_mapping_types])})
        """,
        ui_mapping_types
    ).fetchone()["cnt"]
    
    coverage = 100.0 * entities_with_ui / total_entities if total_entities > 0 else 0
    
    return {
        "status": "REPORT",
        "total_entities": total_entities,
        "entities_with_ui_companions": entities_with_ui,
        "coverage_percent": round(coverage, 2),
        "note": "No threshold set. Reported for awareness.",
    }


def check_cqry_coverage(conn):
    """Confirm .cqry symbols and mappings exist."""
    symbols_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM symbols WHERE kind LIKE 'cqry_%'"
    ).fetchone()["cnt"]
    
    mappings_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM entity_mappings WHERE mapping_type = 'cqry'"
    ).fetchone()["cnt"]
    
    return {
        "status": "PASS" if symbols_count > 0 and mappings_count > 0 else "FAIL",
        "cqry_symbols": symbols_count,
        "cqry_mappings": mappings_count,
        "note": "Both counts should be > 0 per ISSUE-001",
    }


def generate_report(checks_results):
    """Generate the validation report."""
    report = []
    report.append("# Phase 2D Validation Report\n")
    report.append(f"**Generated:** {datetime.now().isoformat()}\n")
    
    # Summary
    passed = sum(1 for r in checks_results.values() if r.get("status") == "PASS")
    failed = sum(1 for r in checks_results.values() if r.get("status") == "FAIL")
    reported = sum(1 for r in checks_results.values() if r.get("status") == "REPORT")
    
    report.append(f"## Summary\n")
    report.append(f"- **Passed:** {passed}\n")
    report.append(f"- **Failed:** {failed}\n")
    report.append(f"- **Reported (informational):** {reported}\n")
    
    if failed > 0:
        report.append("\n**Status: ❌ FAILED** - Some assertions did not pass.\n")
    else:
        report.append("\n**Status: ✅ PASSED** - All assertions passed.\n")
    
    # Detailed results
    report.append("\n---\n")
    report.append("## Detailed Results\n")
    
    check_order = [
        "tables_exist",
        "cqry_coverage",
        "declared_vs_actual_mapping_types",
        "mapping_provenance",
        "workflow_step_ratio",
        "rest_endpoint_coverage",
        "ui_companion_coverage",
    ]
    
    for check_id in check_order:
        if check_id not in checks_results:
            continue
        
        result = checks_results[check_id]
        status = result.get("status", "UNKNOWN")
        status_emoji = "✅" if status == "PASS" else "❌" if status == "FAIL" else "ℹ️"
        
        report.append(f"\n### {status_emoji} {check_id}\n")
        report.append(f"**Status:** {status}\n")
        
        if check_id == "tables_exist":
            report.append(f"- Required: {result['required']}\n")
            report.append(f"- Present: {result['count']}\n")
            if result["missing"]:
                report.append(f"- Missing: {', '.join(result['missing'])}\n")
            report.append(f"- Assertion: All required Phase 2D tables must exist.\n")
        
        elif check_id == "declared_vs_actual_mapping_types":
            report.append(f"**Declared vs Actual Mapping Types:**\n")
            for ext, info in result["results"].items():
                if info.get("status") == "OUT_OF_SCOPE":
                    found_str = "⊘ out of scope"
                    report.append(
                        f"- {ext} ({info['authority']}): {found_str} "
                        f"({info['reason']})\n"
                    )
                else:
                    found_str = "✓ found" if info["found"] else "✗ missing"
                    report.append(
                        f"- {ext} ({info['authority']}): {found_str} "
                        f"(expected: {', '.join(info['expected_types'])})\n"
                    )
            if result["failures"]:
                report.append(f"\n**Failed to find:** {', '.join(result['failures'].keys())}\n")
        
        elif check_id == "mapping_provenance":
            report.append(
                f"- Total mappings: {result['total_mappings']}\n"
                f"- Orphan mappings (no source_text/file_id): {result['orphan_count']}\n"
                f"- Assertion: All mappings must have provenance (orphan_count == 0)\n"
            )
        
        elif check_id == "workflow_step_ratio":
            report.append(
                f"- Workflows: {result['workflows']}\n"
                f"- Workflow steps: {result['workflow_steps']}\n"
                f"- Avg steps per workflow: {result['avg_steps_per_workflow']}\n"
                f"- Note: {result['note']}\n"
            )
        
        elif check_id == "rest_endpoint_coverage":
            report.append(
                f"- Total entities: {result['total_entities']}\n"
                f"- Entities with REST endpoints: {result['entities_with_rest_endpoints']}\n"
                f"- Coverage: {result['coverage_percent']}%\n"
                f"- Note: {result['note']}\n"
            )
        
        elif check_id == "ui_companion_coverage":
            report.append(
                f"- Total entities: {result['total_entities']}\n"
                f"- Entities with UI companions: {result['entities_with_ui_companions']}\n"
                f"- Coverage: {result['coverage_percent']}%\n"
                f"- Note: {result['note']}\n"
            )
        
        elif check_id == "cqry_coverage":
            report.append(
                f"- .cqry symbols: {result['cqry_symbols']}\n"
                f"- .cqry mappings: {result['cqry_mappings']}\n"
                f"- Assertion: Both counts must be > 0\n"
                f"- Note: {result['note']}\n"
            )
    
    return "".join(report)


def main():
    """Run all validation checks and generate report."""
    try:
        conn = get_connection()
        
        # Run all checks
        checks_results = {
            "tables_exist": check_tables_exist(conn),
            "cqry_coverage": check_cqry_coverage(conn),
            "declared_vs_actual_mapping_types": check_declared_vs_actual_mapping_types(conn),
            "mapping_provenance": check_mapping_provenance(conn),
            "workflow_step_ratio": check_workflow_step_ratio(conn),
            "rest_endpoint_coverage": check_rest_endpoint_coverage(conn),
            "ui_companion_coverage": check_ui_companion_coverage(conn),
        }
        
        conn.close()
        
        # Generate report
        report_text = generate_report(checks_results)
        
        # Write report
        report_path = Path(__file__).parent / "phase2d_report.md"
        report_path.write_text(report_text, encoding="utf-8")
        print(f"✅ Report written to {report_path}")
        
        # Determine exit code
        failed = sum(1 for r in checks_results.values() if r.get("status") == "FAIL")
        
        if failed > 0:
            print(f"❌ {failed} assertion(s) failed")
            return 1
        else:
            print("✅ All assertions passed")
            return 0
    
    except Exception as e:
        print(f"❌ Validation failed with error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
