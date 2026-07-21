# catalog/db.py

import sqlite3
from pathlib import Path
from config import CATALOG_DB


_connection = None


def get_connection(db_path: str | None = None):
    global _connection
    if _connection is not None:
        return _connection
    
    path = db_path or CATALOG_DB
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _connection = sqlite3.connect(path)
    _connection.row_factory = sqlite3.Row
    return _connection


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


if __name__ == "__main__":
    init_db()
