#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
import ladybug as lb

from config import CATALOG_DB as SQLITE_DB, GRAPH_DB

def scalar_sql(conn: sqlite3.Connection, q: str) -> int:
    return int(conn.execute(q).fetchone()[0])

def scalar_g(conn: lb.Connection, q: str) -> int:
    res = conn.execute(q)
    return int(res.get_all()[0][0])

def assert_eq(name: str, a: int, b: int) -> None:
    if a != b:
        raise RuntimeError(f"{name} mismatch sqlite={a} graph={b}")

def main() -> None:
    try:
        s = sqlite3.connect(SQLITE_DB)
        g = lb.Connection(lb.Database(GRAPH_DB))

        assert_eq("Entity", scalar_sql(s, "SELECT COUNT(*) FROM entity_nodes"), scalar_g(g, "MATCH (n:Entity) RETURN count(n)"))
        assert_eq("Symbol", scalar_sql(s, "SELECT COUNT(*) FROM symbols"), scalar_g(g, "MATCH (n:Symbol) RETURN count(n)"))
        assert_eq("File", scalar_sql(s, "SELECT COUNT(*) FROM files"), scalar_g(g, "MATCH (n:File) RETURN count(n)"))
        assert_eq("Workflow", scalar_sql(s, "SELECT COUNT(*) FROM workflows"), scalar_g(g, "MATCH (n:Workflow) RETURN count(n)"))
        assert_eq("RestEndpoint", scalar_sql(s, "SELECT COUNT(*) FROM rest_endpoints"), scalar_g(g, "MATCH (n:RestEndpoint) RETURN count(n)"))

        print("Ladybug graph parity validation passed")
    finally:
        g.close()
        s.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Validation failed: {exc}")
        sys.exit(1)