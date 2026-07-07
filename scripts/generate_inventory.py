#!/usr/bin/env python3
"""
Generate project_inventory.json from actual catalog.db state.

Reads:
  - catalog.db: tables, row counts, distinct values
  - filesystem: migrations, parsers, scripts, validators

Emits:
  - project_inventory.json with generated_at, verified_state, declared_state, drift
"""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict


def get_connection():
    db_path = Path(__file__).parent.parent / "catalog" / "catalog.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_verified_state(conn):
    """Extract actual state from catalog.db."""
    verified = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phases": {
            "phase2d": {
                "status": "verified",
                "description": "Phase 2D regenerated from database state"
            }
        },
        "database": {
            "tables": {},
            "total_rows_by_table": {},
            "symbol_kinds": [],
            "mapping_types": [],
            "relationship_types": [],
            "workflow_types": [],
        },
    }
    
    # Get table list and row counts
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row["name"] for row in cursor.fetchall()]
    
    for table in tables:
        verified["database"]["tables"][table] = {
            "status": "present",
            "rows": conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()["cnt"],
        }
    
    # Get distinct symbol kinds
    cursor = conn.execute("SELECT DISTINCT kind FROM symbols ORDER BY kind")
    verified["database"]["symbol_kinds"] = [row["kind"] for row in cursor.fetchall()]
    
    # Get distinct mapping types
    cursor = conn.execute("SELECT DISTINCT mapping_type FROM entity_mappings ORDER BY mapping_type")
    verified["database"]["mapping_types"] = [row["mapping_type"] for row in cursor.fetchall()]
    
    # Get distinct relationship types
    cursor = conn.execute(
        "SELECT DISTINCT relationship_type FROM relationships ORDER BY relationship_type"
    )
    verified["database"]["relationship_types"] = [
        row["relationship_type"] for row in cursor.fetchall()
    ]
    
    # Get distinct workflow types
    cursor = conn.execute("SELECT DISTINCT workflow_type FROM workflows ORDER BY workflow_type")
    verified["database"]["workflow_types"] = [row["workflow_type"] for row in cursor.fetchall()]
    
    return verified


def get_filesystem_state():
    """Extract file system state."""
    repo_root = Path(__file__).parent.parent
    filesystem = {
        "migrations": [],
        "parsers": [],
        "scripts": [],
        "validators": [],
    }
    
    # Migrations
    migrations_dir = repo_root / "migrations"
    if migrations_dir.exists():
        for f in sorted(migrations_dir.glob("*.sql")):
            filesystem["migrations"].append(f.name)
    
    # Parsers
    parsers_dir = repo_root / "parser" / "extractors"
    if parsers_dir.exists():
        for f in sorted(parsers_dir.glob("*_extractor.py")):
            filesystem["parsers"].append(f.name)
    
    # Scripts
    scripts_dir = repo_root / "scripts"
    if scripts_dir.exists():
        for f in sorted(scripts_dir.glob("*.py")):
            if f.name not in ["__pycache__"]:
                filesystem["scripts"].append(f.name)
    
    # Validators
    validators_dir = repo_root / "validation"
    if validators_dir.exists():
        for f in sorted(validators_dir.glob("validate_*.py")):
            filesystem["validators"].append(f.name)
    
    return filesystem


def get_declared_state():
    """Declare design intent (manually maintained)."""
    return {
        "phases": {
            "phase2d": {
                "status": "complete",
                "target_tables": [
                    "rest_endpoints",
                    "ui_companions",
                    "repos",
                    "services",
                    "knowledge_items",
                    "workflow_nodes",
                    "workflow_edges",
                    "openapispec_index",
                ],
                "expected_mapping_types": [
                    "manager",
                    "editor",
                    "lister",
                    "picker",
                    "cqry",
                    "inc",
                    "yaml",
                    "sql",
                    "html",
                    "phtml",
                    "openapispec_schema",
                    "openapispec_operations",
                    "openapispec_history",
                ],
                "required_extractors": [
                    "java_extractor.py",
                    "php_extractor.py",
                    "sql_extractor.py",
                    "xslt_extractor.py",
                    "cqry_extractor.py",
                ],
                "required_validators": [
                    "validate_phase2d.py",
                ],
            }
        }
    }


def compute_drift(verified, declared):
    """Identify differences between verified and declared state."""
    drift = {
        "missing_tables": [],
        "missing_mapping_types": [],
        "missing_extractors": [],
        "missing_validators": [],
        "extra_tables": [],
        "extra_mapping_types": [],
        "extra_extractors": [],
        "extra_validators": [],
    }
    
    # Check tables
    declared_tables = set(declared["phases"]["phase2d"]["target_tables"])
    verified_tables = set(verified["database"]["tables"].keys())
    
    drift["missing_tables"] = sorted(list(declared_tables - verified_tables))
    drift["extra_tables"] = sorted(list(verified_tables - declared_tables))[:20]  # Show first 20
    
    # Check mapping types
    declared_mapping_types = set(declared["phases"]["phase2d"]["expected_mapping_types"])
    verified_mapping_types = set(verified["database"]["mapping_types"])
    
    drift["missing_mapping_types"] = sorted(list(declared_mapping_types - verified_mapping_types))
    drift["extra_mapping_types"] = sorted(list(verified_mapping_types - declared_mapping_types))[:20]
    
    # Check extractors
    declared_extractors = set(declared["phases"]["phase2d"]["required_extractors"])
    verified_extractors = set(verified["filesystem"]["parsers"])
    
    drift["missing_extractors"] = sorted(list(declared_extractors - verified_extractors))
    drift["extra_extractors"] = sorted(list(verified_extractors - declared_extractors))
    
    # Check validators
    declared_validators = set(declared["phases"]["phase2d"]["required_validators"])
    verified_validators = set(verified["filesystem"]["validators"])
    
    drift["missing_validators"] = sorted(list(declared_validators - verified_validators))
    drift["extra_validators"] = sorted(list(verified_validators - declared_validators))
    
    # Remove empty drift entries
    return {k: v for k, v in drift.items() if v}


def generate_inventory():
    """Generate project_inventory.json."""
    try:
        conn = get_connection()
        verified = get_verified_state(conn)
        conn.close()
        
        filesystem = get_filesystem_state()
        verified["filesystem"] = filesystem
        
        declared = get_declared_state()
        
        drift = compute_drift(verified, declared)
        
        # Assemble final inventory
        inventory = {
            "project_name": "intacct-repo-intelligence",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "scripts/generate_inventory.py",
            "verified_state": verified,
            "declared_state": declared,
            "drift": drift if drift else None,
        }
        
        return inventory
    
    except Exception as e:
        print(f"❌ Failed to generate inventory: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return None


def main():
    """Generate and write project_inventory.json."""
    inventory = generate_inventory()
    
    if not inventory:
        return 1
    
    output_path = Path(__file__).parent.parent / "project_inventory.json"
    
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(inventory, f, indent=2)
        
        print(f"✅ Generated {output_path}")
        
        # Print summary
        print("\n## Summary")
        print(f"Generated at: {inventory['generated_at']}")
        
        verified = inventory["verified_state"]["database"]
        print(f"Database state:")
        print(f"  - Tables: {len(verified['tables'])}")
        print(f"  - Symbol kinds: {len(verified['symbol_kinds'])}")
        print(f"  - Mapping types: {len(verified['mapping_types'])}")
        print(f"  - Relationship types: {len(verified['relationship_types'])}")
        print(f"  - Workflow types: {len(verified['workflow_types'])}")
        
        filesystem = inventory["verified_state"]["filesystem"]
        print(f"Filesystem state:")
        print(f"  - Migrations: {len(filesystem['migrations'])}")
        print(f"  - Extractors: {len(filesystem['parsers'])}")
        print(f"  - Scripts: {len(filesystem['scripts'])}")
        print(f"  - Validators: {len(filesystem['validators'])}")
        
        drift = inventory.get("drift")
        if drift:
            print(f"\n⚠️  Drift detected:")
            for key, values in drift.items():
                if values:
                    print(f"  - {key}: {len(values)} items")
                    if len(values) <= 5:
                        for v in values:
                            print(f"    * {v}")
        else:
            print("\n✅ No drift detected - verified and declared states align")
        
        return 0
    
    except Exception as e:
        print(f"❌ Failed to write {output_path}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
