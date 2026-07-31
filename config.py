# config.py
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

REPO_PATH = os.environ.get("REPO_PATH", os.path.expanduser("~/projects/main"))
CATALOG_DB = os.environ.get("CATALOG_DB", str(PROJECT_ROOT / "catalog" / "catalog.db"))
GRAPH_DB = os.environ.get("GRAPH_DB", str(PROJECT_ROOT / "catalog" / "graph.lbug"))

INCLUDE_EXTENSIONS = {
    ".java",
    ".php",
    ".ent",
    ".cqry",
    ".qry",
    ".cls",
    ".inc",
    ".menu",
    ".pol",
    ".phtml",
    ".html",
    ".yaml",
    ".js",
    ".ts",
    ".sql",
    ".xml",
    ".json",
    ".py",
    ".xsl",
    ".xslt",
    ".rpt",
    ".feature",
    ".properties",
}

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "build",
    "dist",
    "target",
    ".idea",
    ".vscode",
    "coverage",
    ".venv*",
    "docs",
    ".codegraph",
    ".gemini",
    ".github",
    ".claude",
}
