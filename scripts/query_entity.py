from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

DEFAULT_DB = os.environ.get("CATALOG_DB", "catalog/catalog.db")


@dataclass
class GraphNode:
    symbol_id: int
    name: str
    kind: str
    depth: int
    via: str | None = None
    direction: str | None = None
    from_symbol: str | None = None


@click.group()
def cli() -> None:
    pass


def get_entity(conn: sqlite3.Connection, entity_name: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, name
        FROM entity_nodes
        WHERE name = ?
        """,
        (entity_name,),
    ).fetchone()


def get_entity_symbols(conn: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            s.id,
            s.name,
            s.kind,
            em.mapping_type,
            em.confidence
        FROM entity_mappings em
        JOIN symbols s
            ON s.id = em.symbol_id
        WHERE em.entity_id = ?
        ORDER BY s.kind, s.name
        """,
        (entity_id,),
    ).fetchall()


def get_root_symbols(
    conn: sqlite3.Connection,
    entity_id: int,
    min_weight: float,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            s.id,
            s.name,
            s.kind,
            er.role,
            er.weight,
            er.reason
        FROM entity_roots er
        JOIN symbols s
            ON s.id = er.symbol_id
        WHERE er.entity_id = ?
          AND er.weight >= ?
        ORDER BY er.weight DESC, s.name
        """,
        (entity_id, min_weight),
    ).fetchall()


def _seed_symbols(
    conn: sqlite3.Connection,
    entity_id: int,
    core_only: bool,
    min_weight: float,
) -> list[sqlite3.Row]:
    if core_only:
        return get_root_symbols(conn, entity_id, min_weight=min_weight)
    return get_entity_symbols(conn, entity_id)


def get_outgoing_relationships(
    conn: sqlite3.Connection,
    symbol_id: int,
    min_confidence: float = 0.0,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            r.relationship_type,
            r.source_symbol_id,
            r.source_name,
            r.source_kind,
            r.target_symbol_id,
            r.target_name,
            r.target_kind,
            r.confidence,
            r.file_path
        FROM relationships r
        WHERE r.source_symbol_id = ?
          AND r.target_symbol_id IS NOT NULL
          AND IFNULL(r.confidence, 0) >= ?
        ORDER BY r.relationship_type, r.target_name
        """,
        (symbol_id, min_confidence),
    ).fetchall()


def get_incoming_relationships(
    conn: sqlite3.Connection,
    symbol_id: int,
    min_confidence: float = 0.0,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            r.relationship_type,
            r.source_symbol_id,
            r.source_name,
            r.source_kind,
            r.target_symbol_id,
            r.target_name,
            r.target_kind,
            r.confidence,
            r.file_path
        FROM relationships r
        WHERE r.target_symbol_id = ?
          AND r.source_symbol_id IS NOT NULL
          AND IFNULL(r.confidence, 0) >= ?
        ORDER BY r.relationship_type, r.source_name
        """,
        (symbol_id, min_confidence),
    ).fetchall()


def get_symbol_files(
    conn: sqlite3.Connection, symbol_id: int, limit: int = 20
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT DISTINCT file_path
        FROM relationships
        WHERE source_symbol_id = ?
           OR target_symbol_id = ?
        ORDER BY file_path
        LIMIT ?
        """,
        (symbol_id, symbol_id, limit),
    ).fetchall()


def print_header(title: str) -> None:
    click.echo("")
    click.echo("=" * 100)
    click.echo(title)
    click.echo("=" * 100)


def print_section(title: str) -> None:
    click.echo("")
    click.echo(title)
    click.echo("-" * len(title))


def group_relationships(rows: list[sqlite3.Row], direction: str) -> None:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)

    for row in rows:
        grouped[row["relationship_type"]].append(row)

    for rel_type in sorted(grouped):
        for row in grouped[rel_type]:
            if direction == "out":
                click.echo(
                    f"    {rel_type:<15} -> "
                    f"{row['target_name']:<45} "
                    f"(confidence={row['confidence']})"
                )
            else:
                click.echo(
                    f"    {rel_type:<15} <- "
                    f"{row['source_name']:<45} "
                    f"(confidence={row['confidence']})"
                )


def show_entity(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    symbols = get_entity_symbols(conn, entity["id"])
    print_header(f"ENTITY: {entity_name}")
    print_section("Mapped Symbols")

    if not symbols:
        click.echo("No symbols mapped.")
        return 0

    for symbol in symbols:
        click.echo(
            f"{symbol['kind']:<15} "
            f"{symbol['name']:<60} "
            f"{(symbol['mapping_type'] or ''):<25} "
            f"confidence={symbol['confidence']}"
        )
    return 0


def show_root_symbols(
    conn: sqlite3.Connection, entity_name: str, min_weight: float
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    roots = get_root_symbols(conn, entity["id"], min_weight=min_weight)

    print_header(f"CORE ENTITY ROOTS: {entity_name}")
    click.echo(f"Min weight: {min_weight}")

    if not roots:
        click.echo(
            f"No canonical roots found at min_weight={min_weight}. "
            "Run build_entity_roots.py build first."
        )
        return 0

    print_section("Entity Roots")
    for root in roots:
        click.echo(
            f"{root['weight']:<8.2f} "
            f"{root['role']:<15} "
            f"{root['kind']:<15} "
            f"{root['name']:<60} "
            f"{root['reason']}"
        )
    return 0


def show_direct_impact(
    conn: sqlite3.Connection,
    entity_name: str,
    min_confidence: float,
    per_symbol_limit: int,
    core_only: bool,
    min_weight: float,
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    seed_symbols = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )

    print_header(f"DIRECT IMPACT REPORT: {entity_name}")
    if core_only:
        click.echo(f"Using canonical roots only (min_weight={min_weight})")
    else:
        click.echo("Using all mapped symbols as seeds")

    print_section("Seed Symbols")
    if not seed_symbols:
        click.echo(
            "No seed symbols found. Try lowering --min-weight or drop --core-only."
        )
        return 0

    for symbol in seed_symbols:
        if "role" in symbol.keys():
            extras = f"role={symbol['role']:<14} weight={symbol['weight']:.2f}"
        else:
            extras = (
                f"mapping={(symbol['mapping_type'] or ''):<20} "
                f"confidence={symbol['confidence']}"
            )

        click.echo(f"{symbol['kind']:<15} {symbol['name']:<60} {extras}")

    print_section("Symbol-Level Impact")
    for symbol in seed_symbols:
        click.echo("")
        click.echo(f"[{symbol['kind']}] {symbol['name']}")

        outgoing = get_outgoing_relationships(conn, symbol["id"], min_confidence)[
            :per_symbol_limit
        ]
        incoming = get_incoming_relationships(conn, symbol["id"], min_confidence)[
            :per_symbol_limit
        ]
        files = get_symbol_files(conn, symbol["id"], limit=10)

        if outgoing:
            click.echo("")
            click.echo("  Depends On:")
            group_relationships(outgoing, "out")

        if incoming:
            click.echo("")
            click.echo("  Referenced By:")
            group_relationships(incoming, "in")

        if files:
            click.echo("")
            click.echo("  Related Files:")
            for file_row in files:
                click.echo(f"    {file_row['file_path']}")
    return 0


def bfs_impact(
    conn: sqlite3.Connection,
    seed_symbols: list[sqlite3.Row],
    max_depth: int,
    min_confidence: float,
    include_incoming: bool,
    include_outgoing: bool,
    max_edges_per_node: int,
) -> list[GraphNode]:
    visited: set[int] = set()
    discovered: list[GraphNode] = []
    queue: deque[GraphNode] = deque()

    for symbol in seed_symbols:
        queue.append(
            GraphNode(
                symbol_id=symbol["id"],
                name=symbol["name"],
                kind=symbol["kind"],
                depth=0,
                via="ENTITY_MAPPING",
                direction="seed",
                from_symbol=None,
            )
        )

    while queue:
        node = queue.popleft()
        if node.symbol_id in visited:
            continue

        visited.add(node.symbol_id)
        discovered.append(node)

        if node.depth >= max_depth:
            continue

        relationships: list[tuple[str, sqlite3.Row]] = []
        if include_outgoing:
            relationships.extend(
                ("out", row)
                for row in get_outgoing_relationships(
                    conn, node.symbol_id, min_confidence
                )[:max_edges_per_node]
            )
        if include_incoming:
            relationships.extend(
                ("in", row)
                for row in get_incoming_relationships(
                    conn, node.symbol_id, min_confidence
                )[:max_edges_per_node]
            )

        for direction, row in relationships:
            if direction == "out":
                next_id = row["target_symbol_id"]
                next_name = row["target_name"]
                next_kind = row["target_kind"] or "unknown"
            else:
                next_id = row["source_symbol_id"]
                next_name = row["source_name"]
                next_kind = row["source_kind"] or "unknown"

            if next_id is None or next_id in visited:
                continue

            queue.append(
                GraphNode(
                    symbol_id=next_id,
                    name=next_name,
                    kind=next_kind,
                    depth=node.depth + 1,
                    via=row["relationship_type"],
                    direction=direction,
                    from_symbol=node.name,
                )
            )

    return discovered


def show_bfs_impact(
    conn: sqlite3.Connection,
    entity_name: str,
    depth: int,
    min_confidence: float,
    include_incoming: bool,
    include_outgoing: bool,
    max_edges_per_node: int,
    core_only: bool,
    min_weight: float,
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    seed_symbols = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )
    if not seed_symbols:
        click.echo(
            f"No seed symbols for entity: {entity_name}. "
            "Try lowering --min-weight or drop --core-only."
        )
        return 0

    print_header(f"TRANSITIVE IMPACT REPORT: {entity_name}")
    click.echo(f"Depth: {depth}")
    click.echo(f"Min confidence: {min_confidence}")
    click.echo(f"Include outgoing dependencies: {include_outgoing}")
    click.echo(f"Include incoming references: {include_incoming}")
    click.echo(f"Max edges per node: {max_edges_per_node}")
    click.echo(f"Core-only seeds: {core_only}")
    click.echo(f"Min seed weight: {min_weight}")

    discovered = bfs_impact(
        conn=conn,
        seed_symbols=seed_symbols,
        max_depth=depth,
        min_confidence=min_confidence,
        include_incoming=include_incoming,
        include_outgoing=include_outgoing,
        max_edges_per_node=max_edges_per_node,
    )

    by_depth: dict[int, list[GraphNode]] = defaultdict(list)
    for node in discovered:
        by_depth[node.depth].append(node)

    for current_depth in sorted(by_depth):
        print_section(f"Depth {current_depth}")
        for node in by_depth[current_depth]:
            if current_depth == 0:
                click.echo(f"{node.kind:<15} {node.name:<60} [seed]")
            else:
                arrow = "->" if node.direction == "out" else "<-"
                click.echo(
                    f"{node.kind:<15} {node.name:<60} {arrow} {node.via} from {node.from_symbol}"
                )

    print_section("Summary")
    click.echo(f"Seed symbols: {len(seed_symbols)}")
    click.echo(f"Discovered symbols: {len(discovered)}")

    kind_counts: dict[str, int] = defaultdict(int)
    for node in discovered:
        kind_counts[node.kind] += 1

    click.echo("")
    click.echo("By kind:")
    for kind, count in sorted(
        kind_counts.items(), key=lambda item: item[1], reverse=True
    ):
        click.echo(f"  {kind:<15} {count}")
    return 0


def show_risk_summary(
    conn: sqlite3.Connection,
    entity_name: str,
    depth: int,
    min_confidence: float,
    max_edges_per_node: int,
    core_only: bool,
    min_weight: float,
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    seed_symbols = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )
    if not seed_symbols:
        click.echo(
            f"No seed symbols for entity: {entity_name}. "
            "Try lowering --min-weight or drop --core-only."
        )
        return 0

    discovered = bfs_impact(
        conn=conn,
        seed_symbols=seed_symbols,
        max_depth=depth,
        min_confidence=min_confidence,
        include_incoming=True,
        include_outgoing=True,
        max_edges_per_node=max_edges_per_node,
    )

    incoming_count = 0
    outgoing_count = 0
    expansion_points: dict[str, int] = defaultdict(int)

    for node in discovered:
        if node.direction == "in":
            incoming_count += 1
        elif node.direction == "out":
            outgoing_count += 1
        expansion_points[node.from_symbol or "seed"] += 1

    print_header(f"RISK SUMMARY: {entity_name}")
    click.echo(f"Core-only seeds: {core_only}")
    click.echo(f"Min seed weight: {min_weight}")
    click.echo(f"Mapped seed symbols: {len(seed_symbols)}")
    click.echo(f"Total discovered symbols: {len(discovered)}")
    click.echo(f"Incoming impact references: {incoming_count}")
    click.echo(f"Outgoing dependencies: {outgoing_count}")

    print_section("Top Expansion Points")
    top = sorted(expansion_points.items(), key=lambda item: item[1], reverse=True)[:20]
    for name, count in top:
        click.echo(f"{name:<60} {count}")
    return 0


def _resolve_direction_flags(
    incoming_only: bool, outgoing_only: bool
) -> tuple[bool, bool]:
    if incoming_only and outgoing_only:
        raise click.ClickException("Use only one of --incoming-only or --outgoing-only")

    include_incoming = not outgoing_only
    include_outgoing = not incoming_only
    return include_incoming, include_outgoing


@cli.command("entity")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--workflow", is_flag=True, help="Show discovered workflows for the entity."
)
@click.option("--flow", is_flag=True, help="Show end-to-end flow view for the entity.")
@click.option(
    "--openapispec", is_flag=True, help="Show openapispec mappings for the entity."
)
@click.option("--access", is_flag=True, help="Show linked entity access graph records.")
def entity(
    entity_name: str,
    db: str,
    workflow: bool,
    flow: bool,
    openapispec: bool,
    access: bool,
) -> None:
    """Show mapped symbols for an entity."""
    selected_views = sum([workflow, flow, openapispec, access])
    if selected_views > 1:
        raise click.ClickException(
            "Use only one of --workflow, --flow, --openapispec, or --access"
        )

    conn = get_connection(db)
    try:
        if workflow:
            raise SystemExit(show_workflow_view(conn, entity_name))

        if flow:
            raise SystemExit(show_flow_view(conn, entity_name))

        if openapispec:
            raise SystemExit(show_openapispec_view(conn, entity_name))

        if access:
            raise SystemExit(show_access_view(conn, entity_name))

        raise SystemExit(show_entity(conn, entity_name))
    finally:
        conn.close()


@cli.command("root-symbols")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
def root_symbols(entity_name: str, db: str, min_weight: float) -> None:
    """Show canonical roots for an entity."""
    conn = get_connection(db)
    try:
        raise SystemExit(show_root_symbols(conn, entity_name, min_weight))
    finally:
        conn.close()


@cli.command("direct-impact")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--min-confidence", type=float, default=0.0, show_default=True)
@click.option(
    "--core-only",
    is_flag=True,
    help="Use only canonical entity roots as traversal seeds.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
@click.option(
    "--per-symbol-limit",
    "--limit",
    "per_symbol_limit",
    type=int,
    default=25,
    show_default=True,
    help="Maximum relationships per symbol.",
)
def direct_impact(
    entity_name: str,
    db: str,
    min_confidence: float,
    core_only: bool,
    min_weight: float,
    per_symbol_limit: int,
) -> None:
    """Show direct symbol-level incoming/outgoing relationships."""
    conn = get_connection(db)
    try:
        raise SystemExit(
            show_direct_impact(
                conn=conn,
                entity_name=entity_name,
                min_confidence=min_confidence,
                per_symbol_limit=per_symbol_limit,
                core_only=core_only,
                min_weight=min_weight,
            )
        )
    finally:
        conn.close()


@cli.command("impact")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--depth", type=int, default=1, show_default=True, help="Traversal depth."
)
@click.option("--min-confidence", type=float, default=0.0, show_default=True)
@click.option("--incoming-only", is_flag=True, help="Only include incoming references.")
@click.option(
    "--outgoing-only", is_flag=True, help="Only include outgoing dependencies."
)
@click.option(
    "--core-only",
    is_flag=True,
    help="Use only canonical entity roots as traversal seeds.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
@click.option(
    "--max-edges-per-node",
    "--limit",
    "max_edges_per_node",
    type=int,
    default=25,
    show_default=True,
    help="Maximum traversed relationships per symbol.",
)
def impact(
    entity_name: str,
    db: str,
    depth: int,
    min_confidence: float,
    incoming_only: bool,
    outgoing_only: bool,
    core_only: bool,
    min_weight: float,
    max_edges_per_node: int,
) -> None:
    """Show impact analysis; depth=1 uses direct impact, depth>1 uses BFS traversal."""
    include_incoming, include_outgoing = _resolve_direction_flags(
        incoming_only, outgoing_only
    )
    conn = get_connection(db)
    try:
        if depth <= 1:
            raise SystemExit(
                show_direct_impact(
                    conn=conn,
                    entity_name=entity_name,
                    min_confidence=min_confidence,
                    per_symbol_limit=max_edges_per_node,
                    core_only=core_only,
                    min_weight=min_weight,
                )
            )

        raise SystemExit(
            show_bfs_impact(
                conn=conn,
                entity_name=entity_name,
                depth=depth,
                min_confidence=min_confidence,
                include_incoming=include_incoming,
                include_outgoing=include_outgoing,
                max_edges_per_node=max_edges_per_node,
                core_only=core_only,
                min_weight=min_weight,
            )
        )
    finally:
        conn.close()


@cli.command("risk")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--depth", type=int, default=2, show_default=True)
@click.option("--min-confidence", type=float, default=0.0, show_default=True)
@click.option(
    "--core-only",
    is_flag=True,
    help="Use only canonical entity roots as traversal seeds.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
@click.option("--max-edges-per-node", type=int, default=25, show_default=True)
def risk(
    entity_name: str,
    db: str,
    depth: int,
    min_confidence: float,
    core_only: bool,
    min_weight: float,
    max_edges_per_node: int,
) -> None:
    """Show compact risk summary for transitive impact."""
    conn = get_connection(db)
    try:
        raise SystemExit(
            show_risk_summary(
                conn=conn,
                entity_name=entity_name,
                depth=depth,
                min_confidence=min_confidence,
                max_edges_per_node=max_edges_per_node,
                core_only=core_only,
                min_weight=min_weight,
            )
        )
    finally:
        conn.close()


def get_workflows(
    conn: sqlite3.Connection,
    entity_id: int,
    workflow_type: str | None = None,
) -> list[sqlite3.Row]:
    if workflow_type:
        return conn.execute(
            """
            SELECT id, name, workflow_type, source_kind, source_file
            FROM workflows
            WHERE entity_id = ?
              AND workflow_type = ?
            ORDER BY workflow_type, name
            """,
            (entity_id, workflow_type),
        ).fetchall()

    return conn.execute(
        """
        SELECT id, name, workflow_type, source_kind, source_file
        FROM workflows
        WHERE entity_id = ?
        ORDER BY workflow_type, name
        """,
        (entity_id,),
    ).fetchall()


def show_workflow_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    wfs = get_workflows(conn, entity["id"])

    print_header(f"WORKFLOWS: {entity_name}")

    if not wfs:
        click.echo("No workflows discovered.")
        return 0

    by_type: dict[str, list[sqlite3.Row]] = {}
    for wf in wfs:
        by_type.setdefault(wf["workflow_type"], []).append(wf)

    for wf_type in sorted(by_type.keys()):
        print_section(wf_type.upper())
        for wf in by_type[wf_type]:
            src = wf["source_kind"]
            source_file = wf["source_file"] or ""
            click.echo(f"  {wf['name']}   [source={src} {source_file}]")

    return 0


def show_flow_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    print_header(f"END-TO-END FLOW: {entity_name}")

    roots = get_root_symbols(conn, entity["id"], 0.75)

    print_section("Core Roots (>= 0.75)")
    for r in roots:
        click.echo(f"  {r['role']:<28} {r['name']}")

    db_tables = conn.execute(
        """
        SELECT dt.table_name, dt.primary_keys,
               COUNT(df.id) AS field_count
        FROM entity_nodes en
        JOIN dbschema_tables dt ON LOWER(dt.table_name) = LOWER(en.table_name)
        LEFT JOIN dbschema_fields df ON df.dbschema_table_id = dt.id
        WHERE en.id = ?
        GROUP BY dt.id
        ORDER BY dt.table_name
        """,
        (entity["id"],),
    ).fetchall()

    print_section("DB Schema")
    if db_tables:
        for t in db_tables:
            pkeys = t["primary_keys"] or ""
            pkey_str = f"  pk=[{pkeys}]" if pkeys else ""
            click.echo(f"  {t['table_name']:<40} {t['field_count']} fields{pkey_str}")
    else:
        click.echo("  no db table mapped (entity_nodes.table_name is NULL or not in dbschema)")

    wfs = get_workflows(conn, entity["id"])

    if wfs:
        by_type: dict[str, list[sqlite3.Row]] = {}
        for wf in wfs:
            by_type.setdefault(wf["workflow_type"], []).append(wf)

        for wf_type in sorted(by_type.keys()):
            print_section(f"{wf_type} workflows")
            for wf in by_type[wf_type]:
                src = wf["source_kind"]
                source_file = wf["source_file"] or ""
                click.echo(f"  {wf['name']}   [source={src} {source_file}]")
    else:
        print_section("Workflows")
        click.echo("  none discovered yet")

    return 0


def show_access_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    rows = conn.execute(
        """
        SELECT
            eal.surface,
            eal.record_id,
            eal.link_type,
            eal.evidence_file_id,
            ef.path AS evidence_file,
            eal.notes,
            CASE
                WHEN eal.surface = 'security_operation' THEN (
                    SELECT so.op_key
                    FROM security_operations so
                    WHERE so.id = eal.record_id
                )
                WHEN eal.surface = 'security_policy' THEN (
                    SELECT sp.policy_name
                    FROM security_policies sp
                    WHERE sp.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu' THEN (
                    SELECT COALESCE(sm.menu_name, sm.module, '(menu)')
                    FROM security_menus sm
                    WHERE sm.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu_item' THEN (
                    SELECT smi.item_path
                    FROM security_menu_items smi
                    WHERE smi.id = eal.record_id
                )
                WHEN eal.surface = 'dbschema_table' THEN (
                    SELECT dt.table_name
                    FROM dbschema_tables dt
                    WHERE dt.id = eal.record_id
                )
                WHEN eal.surface = 'workflow' THEN (
                    SELECT wf.name
                    FROM workflows wf
                    WHERE wf.id = eal.record_id
                )
                WHEN eal.surface = 'rest_endpoint' THEN (
                    SELECT re.method || ' ' || re.path
                    FROM rest_endpoints re
                    WHERE re.id = eal.record_id
                )
                ELSE '(unknown)'
            END AS label,
            CASE
                WHEN eal.surface = 'security_operation' THEN (
                    SELECT so.source_file
                    FROM security_operations so
                    WHERE so.id = eal.record_id
                )
                WHEN eal.surface = 'security_policy' THEN (
                    SELECT sp.source_file
                    FROM security_policies sp
                    WHERE sp.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu' THEN (
                    SELECT sm.source_file
                    FROM security_menus sm
                    WHERE sm.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu_item' THEN (
                    SELECT sm.source_file
                    FROM security_menu_items smi
                    JOIN security_menus sm ON sm.id = smi.menu_id
                    WHERE smi.id = eal.record_id
                )
                WHEN eal.surface = 'dbschema_table' THEN (
                    SELECT dt.source_file
                    FROM dbschema_tables dt
                    WHERE dt.id = eal.record_id
                )
                WHEN eal.surface = 'workflow' THEN (
                    SELECT wf.source_file
                    FROM workflows wf
                    WHERE wf.id = eal.record_id
                )
                WHEN eal.surface = 'rest_endpoint' THEN (
                    SELECT f.path
                    FROM rest_endpoints re
                    JOIN files f ON f.id = re.file_id
                    WHERE re.id = eal.record_id
                )
                ELSE NULL
            END AS source_file
        FROM entity_access_links eal
        LEFT JOIN files ef
          ON ef.id = eal.evidence_file_id
        WHERE eal.entity_id = ?
        ORDER BY eal.surface, eal.link_type, label
        """,
        (entity["id"],),
    ).fetchall()

    print_header(f"ENTITY ACCESS GRAPH: {entity_name}")

    if not rows:
        click.echo(
            "No entity access links found. Run build_entity_access_links.py build first."
        )
        return 0

    current_surface = None
    for row in rows:
        if current_surface != row["surface"]:
            current_surface = row["surface"]
            print_section(current_surface.upper())

        source_file = row["source_file"] or ""
        evidence_file = row["evidence_file"] or ""
        click.echo(
            f"  [{row['link_type']:<15}] {row['label']} "
            f"(record_id={row['record_id']}, source={source_file}, evidence={evidence_file})"
        )

        if row["surface"] == "dbschema_table":
            fields = conn.execute(
                """
                SELECT field_name, field_type
                FROM dbschema_fields
                WHERE dbschema_table_id = ?
                ORDER BY field_name
                """,
                (row["record_id"],),
            ).fetchall()
            if fields:
                for f in fields:
                    ftype = f["field_type"] or "?"
                    click.echo(f"    {f['field_name']:<40} {ftype}")

    return 0


def show_openapispec_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    rows = conn.execute(
        """
        SELECT source_text, mapping_type
        FROM entity_mappings
        WHERE entity_id = ?
          AND mapping_type LIKE 'openapispec_%'
        ORDER BY mapping_type, source_text
        """,
        (entity["id"],),
    ).fetchall()

    print_header(f"OPENAPI SPEC FILES: {entity_name}")

    if not rows:
        click.echo("No openapispec mappings found.")
        return 0

    for r in rows:
        click.echo(f"[{r['mapping_type']:<25}] {r['source_text']}")
    return 0


if __name__ == "__main__":
    cli()
