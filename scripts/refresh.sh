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
python scripts/scan_ent_files.py scan --repo-root "$REPO_ROOT"
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
# Phase 7: OpenAPI Specification Linking
# ===================================================================
echo "🌐 Phase 7: Linking OpenAPI specifications..."
echo "   Connecting API entities to OpenAPI spec files"
python scripts/link_openapispec.py link
if [ $? -ne 0 ]; then
  echo "   ⚠️  OpenAPI linking completed with warnings (non-fatal)"
else
  echo "   ✅ OpenAPI linking complete"
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
    ('relationships', 'Symbol relationships'),
]
for table, desc in tables:
    try:
        count = cur.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        print(f'   ✓ {table}: {count:,} records ({desc})')
    except:
        print(f'   ✗ {table}: Not available')
conn.close()
"
echo ""
echo "=================================================="
