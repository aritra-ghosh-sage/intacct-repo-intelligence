#!/usr/bin/env bash
set -e

# Navigate to the intacct-repo-intelligence directory
cd ~/projects/intacct-repo-intelligence
source .venv/bin/activate

# Track start time
START_TIME=$(date +%s)
echo "=================================================="
echo "🚀 Catalog Database Refresh Started"
echo "=================================================="
echo ""

# ===================================================================
# Phase 1: Blank Slate Preparation
# ===================================================================
echo "🧹 Phase 1: Preparing blank slate..."
if [ -f catalog/catalog.db ]; then
  echo "   Removing existing catalog.db..."
  rm -f catalog/catalog.db
fi

echo "   Initializing fresh database schema..."
python -c "
from catalog.db import init_db
try:
    init_db()
    print('   ✅ Database initialized successfully')
except Exception as e:
    print(f'   ❌ Error initializing database: {e}')
    exit(1)
"
echo ""

# ===================================================================
# Phase 2: Repository Scanning
# ===================================================================
echo "📂 Phase 2: Scanning repository..."
echo "   Indexing all source files from /home/aritraghosh/projects/main"
python -m parser.scan_repo
echo "   ✅ Repository scan complete"
echo ""

# ===================================================================
# Phase 3: Symbol Extraction
# ===================================================================
echo "🔍 Phase 3: Extracting symbols..."
echo "   Parsing files for symbols, classes, functions, and relationships"
python -m parser.extract_symbols --full
echo "   ✅ Symbol extraction complete"
echo ""

# ===================================================================
# Phase 4: ENT File Processing
# ===================================================================
echo "📋 Phase 4: Processing ENT files..."
echo "   Scanning PHP entity files for entity metadata"
REPO_ROOT="/home/aritraghosh/projects/main"
python scripts/scan_ent_files.py --repo-root "$REPO_ROOT"
if [ $? -ne 0 ]; then
  echo "   ⚠️  ENT file scanning completed with warnings (non-fatal)"
else
  echo "   ✅ ENT file scan complete"
fi
echo ""

# ===================================================================
# Phase 5: Entity Building
# ===================================================================
echo "🏗️  Phase 5: Building entity nodes..."
echo "   Creating entity_nodes from entity definitions"
python scripts/build_entities.py build
if [ $? -ne 0 ]; then
  echo "   ⚠️  Entity building completed with warnings (non-fatal)"
else
  echo "   ✅ Entity building complete"
fi
echo ""

# ===================================================================
# Phase 6: Entity Root Linking
# ===================================================================
echo "🔗 Phase 6: Building entity roots..."
echo "   Linking symbols to entities as root definitions"
python scripts/build_entity_roots.py build
if [ $? -ne 0 ]; then
  echo "   ⚠️  Entity root building completed with warnings (non-fatal)"
else
  echo "   ✅ Entity roots built successfully"
fi
echo ""

# ===================================================================
# Phase 7: Relationship Extraction
# ===================================================================
echo "🔗 Phase 7: Extracting symbol relationships..."
echo "   Analyzing code to identify relationships (INHERITS, IMPLEMENTS, IMPORTS, etc.)"
python -m parser.extract_relationships --repo-root "/home/aritraghosh/projects/main"
if [ $? -ne 0 ]; then
  echo "   ⚠️  Relationship extraction completed with warnings (non-fatal)"
else
  echo "   ✅ Relationship extraction complete"
fi
echo ""

# ===================================================================
# Phase 8: Workflow Building
# ===================================================================
echo "🔄 Phase 8: Building workflows..."
echo "   Extracting workflow definitions from entity mappings and YAML handlers"
python scripts/build_workflows.py build --db catalog/catalog.db --repo-root "/home/aritraghosh/projects/main"
if [ $? -ne 0 ]; then
  echo "   ⚠️  Workflow building completed with warnings (non-fatal)"
else
  echo "   ✅ Workflow building complete"
fi
echo ""

# ===================================================================
# Phase 9: Security Mapping Build
# ===================================================================
echo "🔐 Phase 9: Building security/menu/dbschema mappings..."
echo "   Extracting security operation keys/ids, policy eops, menu links, and dbschema metadata"
python scripts/build_security_mappings.py build --db catalog/catalog.db --repo-root "/home/aritraghosh/projects/main"
if [ $? -ne 0 ]; then
  echo "   ⚠️  Security mapping build completed with warnings (non-fatal)"
else
  echo "   ✅ Security mapping build complete"
fi
echo ""

# ===================================================================
# Phase 10: OpenAPI Specification Scanning
# ===================================================================
echo "📚 Phase 10: Scanning OpenAPI specifications..."
echo "   Indexing OpenAPI YAML specification files"
python scripts/scan_openapispec.py scan --db catalog/catalog.db --repo-root "/home/aritraghosh/projects/main"
if [ $? -ne 0 ]; then
  echo "   ⚠️  OpenAPI scanning completed with warnings (non-fatal)"
else
  echo "   ✅ OpenAPI scanning complete"
fi
echo ""

# ===================================================================
# Phase 11: REST Endpoints Extraction
# ===================================================================
echo "🌐 Phase 11: Extracting REST endpoints..."
echo "   Parsing OpenAPI files from openapispec_index to extract REST API paths and methods"
python scripts/build_rest_endpoints.py build --db catalog/catalog.db --repo-root "/home/aritraghosh/projects/main"
if [ $? -ne 0 ]; then
  echo "   ⚠️  REST endpoints extraction completed with warnings (non-fatal)"
else
  echo "   ✅ REST endpoints extraction complete"
fi
echo ""

# ===================================================================
# Phase 12: OpenAPI Specification Linking
# ===================================================================
echo "🔗 Phase 12: Linking OpenAPI specifications to entities..."
echo "   Connecting API entities to OpenAPI spec files (kinds: operations, schemas, etc.)"
python scripts/link_openapispec.py link --db catalog/catalog.db
if [ $? -ne 0 ]; then
  echo "   ⚠️  OpenAPI linking completed with warnings (non-fatal)"
else
  echo "   ✅ OpenAPI linking complete"
fi
echo ""

# ===================================================================
# Phase 13: Entity Access Graph Linking
# ===================================================================
echo "🔗 Phase 13: Building entity access graph links..."
echo "   Creating deterministic entity bridges for security/menu/dbschema/workflow/rest evidence"
python scripts/build_entity_access_links.py build --db catalog/catalog.db --reset
if [ $? -ne 0 ]; then
  echo "   ⚠️  Entity access linking completed with warnings (non-fatal)"
else
  echo "   ✅ Entity access linking complete"
fi
echo ""

# ===================================================================
# Summary
# ===================================================================
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
echo "=================================================="
echo "✅ Catalog Refresh Complete!"
echo "=================================================="
echo "Duration: ${DURATION}s"
echo ""
echo "📊 Database Status:"
echo "   Location: ~/projects/intacct-repo-intelligence/catalog/catalog.db"
python -c "
import sqlite3
conn = sqlite3.connect('catalog/catalog.db')
cur = conn.cursor()
tables = [
    ('files', 'Source files indexed'),
    ('symbols', 'Symbols extracted'),
    ('entity_nodes', 'Entity definitions'),
    ('entity_roots', 'Entity root mappings'),
    ('entity_mappings', 'Entity-symbol mappings'),
    ('relationships', 'Symbol relationships'),
    ('workflows', 'Workflow definitions'),
    ('security_operations', 'Security operation keys and metadata'),
    ('security_policy_eops', 'Policy values to operation keys'),
    ('security_menu_items', 'Menu items and MENU_KEY mappings'),
    ('dbschema_fields', 'dbschema table fields'),
    ('openapispec_index', 'OpenAPI specs indexed'),
    ('rest_endpoints', 'REST API endpoints'),
    ('entity_access_links', 'Entity access graph links'),
]
for table, desc in tables:
    try:
        count = cur.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        status = '✓' if count > 0 else '⚠'
        print(f'   {status} {table:20} {count:>10,} records ({desc})')
    except:
        print(f'   ✗ {table:20} Error reading table')
conn.close()
"
echo ""
echo "=================================================="
