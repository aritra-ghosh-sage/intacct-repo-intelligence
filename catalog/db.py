# catalog/db.py

import sqlite3
from pathlib import Path

from config import CATALOG_DB

_connection: sqlite3.Connection | None = None
_connection_path: str | None = None


def get_connection(db_path: str | None = None):
    """Return a live connection for ``db_path``.

    The historical singleton returned a closed or wrong-path connection when
    multiple catalog files were used in one process. Candidate refreshes need
    to open the temporary catalog deterministically.
    """
    global _connection, _connection_path
    path = str(Path(db_path or CATALOG_DB).resolve())
    if _connection is not None and _connection_path == path:
        try:
            _connection.execute("SELECT 1")
            return _connection
        except sqlite3.ProgrammingError:
            _connection = None
            _connection_path = None

    if _connection is not None:
        try:
            _connection.close()
        except sqlite3.Error:
            pass

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _connection = sqlite3.connect(path)
    _connection.execute("PRAGMA foreign_keys = ON")
    _connection.row_factory = sqlite3.Row
    _connection_path = path
    return _connection


def require_foreign_key_integrity(conn: sqlite3.Connection, *, context: str) -> None:
    """Fail a mutation before it can publish a catalog with orphaned facts."""
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        sample = [tuple(row) for row in violations[:5]]
        raise RuntimeError(
            f"foreign key integrity failed after {context}: "
            f"{len(violations)} violation(s); sample={sample}"
        )


def init_db():
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path, "r") as f:
        schema = f.read()

    conn = get_connection()
    try:
        conn.executescript(schema)
        conn.commit()
        print(f"✅ Initialized DB at {CATALOG_DB}")
    finally:
        conn.close()


def migrate_multi_repo(
    *, db_path: str | None = None, local_root: str, tracked_branch: str = "main"
):
    """Apply the repository-scoping migration to an existing catalog.

    This is intentionally separate from ``init_db``: fresh databases receive
    the current schema, while existing catalogs need a table rebuild that
    preserves file IDs and their provenance references.
    """
    from catalog.migrations import apply_multi_repo_migration

    path = db_path or CATALOG_DB
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        apply_multi_repo_migration(
            conn, local_root=local_root, tracked_branch=tracked_branch
        )
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
