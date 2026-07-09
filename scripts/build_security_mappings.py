#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import click
from tqdm import tqdm
from tree_sitter_languages import get_parser

from catalog.db import get_connection

DEFAULT_DB = "catalog/catalog.db"
DEFAULT_REPO_ROOT = "/home/aritraghosh/projects/main"
OUTPUT_DIR = Path("outputs")
PARSE_FAILURES_LOG = OUTPUT_DIR / "security_parse_failures.jsonl"
UNRESOLVED_LOG = OUTPUT_DIR / "security_unresolved_keys.jsonl"
CONFLICTS_LOG = OUTPUT_DIR / "security_conflicts.jsonl"


@dataclass
class PhpArray:
    items: list[tuple[Any | None, Any]]


_php_parser = get_parser("php")


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _decode_php_string(value: str) -> str:
    if len(value) < 2:
        return value
    quote = value[0]
    if quote not in {"'", '"'} or value[-1] != quote:
        return value
    body = value[1:-1]
    out: list[str] = []
    idx = 0
    while idx < len(body):
        ch = body[idx]
        if ch != "\\" or idx + 1 >= len(body):
            out.append(ch)
            idx += 1
            continue
        nxt = body[idx + 1]
        if quote == "'" and nxt not in {"\\", "'"}:
            out.append("\\")
            out.append(nxt)
        elif quote == '"':
            escaped = {
                "n": "\n",
                "r": "\r",
                "t": "\t",
                '"': '"',
                "\\": "\\",
                "$": "$",
            }.get(nxt, nxt)
            out.append(escaped)
        else:
            out.append(nxt)
        idx += 2
    return "".join(out)


def _extract_first_string_literal(node: Any, source: bytes) -> str | None:
    """Extract first string literal from expression (for partial unresolved values)."""
    if node.type == "string":
        return _decode_php_string(_node_text(node, source).strip())
    if node.type == "binary_expression":
        for child in node.children:
            if child.type == "string":
                return _decode_php_string(_node_text(child, source).strip())
    for child in node.children:
        result = _extract_first_string_literal(child, source)
        if result is not None:
            return result
    return None


def _tree_sitter_to_value(node: Any, source: bytes) -> Any:
    node_type = node.type
    text = _node_text(node, source).strip()

    if node_type == "array_creation_expression":
        items: list[tuple[Any | None, Any]] = []
        for child in node.children:
            if child.type != "array_element_initializer":
                continue
            named = list(child.named_children)
            if len(named) == 1:
                items.append((None, _tree_sitter_to_value(named[0], source)))
                continue
            if len(named) == 2:
                key = _tree_sitter_to_value(named[0], source)
                value = _tree_sitter_to_value(named[1], source)
                items.append((key, value))
                continue
        return PhpArray(items)

    if node_type == "string":
        return _decode_php_string(text)
    if node_type == "integer":
        try:
            return int(text, 10)
        except ValueError:
            return text
    if node_type == "float":
        try:
            return float(text)
        except ValueError:
            return text
    if node_type == "boolean":
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    if node_type == "name":
        lowered = text.lower()
        if lowered == "null":
            return None
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return text
    if node_type == "binary_expression":
        first_str = _extract_first_string_literal(node, source)
        if first_str is not None:
            return first_str
    if node_type == "call_expression":
        first_str = _extract_first_string_literal(node, source)
        if first_str is not None:
            return first_str
    return text


@dataclass
class BuildStats:
    files_discovered: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    operations_inserted: int = 0
    allowops_inserted: int = 0
    policies_inserted: int = 0
    policy_values_inserted: int = 0
    policy_eops_inserted: int = 0
    menus_inserted: int = 0
    menu_items_inserted: int = 0
    menu_links_inserted: int = 0
    dbschema_tables_inserted: int = 0
    dbschema_fields_inserted: int = 0
    unresolved_policy_keys: int = 0
    unresolved_menu_keys: int = 0
    conflicts_detected: int = 0
    missing_includes: int = 0

    @property
    def unresolved_total(self) -> int:
        return self.unresolved_policy_keys + self.unresolved_menu_keys


def ensure_security_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS security_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op_key TEXT NOT NULL,
            op_numeric_id INTEGER,
            title TEXT,
            action TEXT,
            script TEXT,
            force_mode TEXT,
            secure_only INTEGER,
            allow_dev_env_only INTEGER,
            source_file TEXT NOT NULL,
            source_line INTEGER,
            source_kind TEXT NOT NULL,
            raw_hash TEXT,
            UNIQUE(op_key, op_numeric_id, source_file, source_kind)
        );
        CREATE TABLE IF NOT EXISTS security_operation_allowops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id INTEGER NOT NULL,
            allowed_op_key TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_line INTEGER,
            FOREIGN KEY(operation_id) REFERENCES security_operations(id) ON DELETE CASCADE,
            UNIQUE(operation_id, allowed_op_key, source_file)
        );
        CREATE TABLE IF NOT EXISTS security_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_name TEXT NOT NULL,
            module TEXT,
            label TEXT,
            source_file TEXT NOT NULL,
            source_line INTEGER,
            UNIQUE(policy_name, source_file)
        );
        CREATE TABLE IF NOT EXISTS security_policy_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id INTEGER NOT NULL,
            value_key TEXT NOT NULL,
            display TEXT,
            value_label TEXT,
            source_line INTEGER,
            FOREIGN KEY(policy_id) REFERENCES security_policies(id) ON DELETE CASCADE,
            UNIQUE(policy_id, value_key)
        );
        CREATE TABLE IF NOT EXISTS security_policy_eops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_value_id INTEGER NOT NULL,
            op_key TEXT NOT NULL,
            source_line INTEGER,
            FOREIGN KEY(policy_value_id) REFERENCES security_policy_values(id) ON DELETE CASCADE,
            UNIQUE(policy_value_id, op_key)
        );
        CREATE TABLE IF NOT EXISTS security_menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT,
            menu_name TEXT,
            source_file TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS security_menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            item_path TEXT NOT NULL,
            item_name TEXT NOT NULL,
            menu_item_id TEXT,
            menu_script TEXT,
            menu_key TEXT,
            source_line INTEGER,
            FOREIGN KEY(menu_id) REFERENCES security_menus(id) ON DELETE CASCADE,
            UNIQUE(menu_id, item_path)
        );
        CREATE TABLE IF NOT EXISTS security_menu_op_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_item_id INTEGER NOT NULL,
            op_key TEXT NOT NULL,
            operation_id INTEGER,
            resolution_reason TEXT NOT NULL,
            FOREIGN KEY(menu_item_id) REFERENCES security_menu_items(id) ON DELETE CASCADE,
            FOREIGN KEY(operation_id) REFERENCES security_operations(id) ON DELETE SET NULL,
            UNIQUE(menu_item_id, op_key)
        );
        CREATE TABLE IF NOT EXISTS dbschema_tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            primary_keys TEXT,
            source_file TEXT NOT NULL,
            source_line INTEGER,
            raw_hash TEXT,
            UNIQUE(table_name, source_file)
        );
        CREATE TABLE IF NOT EXISTS dbschema_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dbschema_table_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            field_type TEXT,
            source_line INTEGER,
            FOREIGN KEY(dbschema_table_id) REFERENCES dbschema_tables(id) ON DELETE CASCADE,
            UNIQUE(dbschema_table_id, field_name)
        );
        """
    )
    conn.commit()


def reset_security_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DELETE FROM security_menu_op_links;
        DELETE FROM security_menu_items;
        DELETE FROM security_menus;
        DELETE FROM security_policy_eops;
        DELETE FROM security_policy_values;
        DELETE FROM security_policies;
        DELETE FROM security_operation_allowops;
        DELETE FROM security_operations;
        DELETE FROM dbschema_fields;
        DELETE FROM dbschema_tables;
        """
    )
    conn.commit()


def normalize_bool(value: Any) -> int | None:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) != 0 else 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "on", "1"}:
            return 1
        if lowered in {"false", "no", "off", "0"}:
            return 0
    return None


def normalize_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        out = value.strip()
        return out if out else None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def normalize_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"-?\d+", stripped):
            return int(stripped)
    return None


def array_to_map(node: Any) -> dict[str, Any]:
    if not isinstance(node, PhpArray):
        return {}
    out: dict[str, Any] = {}
    for key, value in node.items:
        if key is None:
            continue
        key_str = normalize_string(key)
        if key_str is None:
            continue
        out[key_str] = value
    return out


def array_to_list(node: Any) -> list[Any]:
    if not isinstance(node, PhpArray):
        return []
    return [value for key, value in node.items if key is None]


def parse_assigned_array(text: str, var_name: str) -> PhpArray | None:
    assigned = parse_all_assigned_arrays(text)
    return assigned.get(var_name)


def parse_all_assigned_arrays(
    text: str,
    parse_failures: list[dict[str, Any]] | None = None,
    file_path: str | None = None,
) -> dict[str, PhpArray]:
    """Parse all PHP variable assignments to arrays using tree-sitter.

    Falls back to regex-based discovery if tree-sitter parse fails.
    All extracted values are tree-sitter-based (no legacy PhpArrayParser fallback).
    """
    source = text.encode("utf-8", errors="replace")
    try:
        tree = _php_parser.parse(source)
    except Exception as exc:
        if parse_failures is not None:
            parse_failures.append(
                {
                    "category": "parse_error",
                    "file_path": file_path or "<unknown>",
                    "reason": f"tree-sitter parse failed: {str(exc)}",
                }
            )
        return {}

    out: dict[str, PhpArray] = {}

    def walk(node: Any) -> None:
        if node.type == "assignment_expression":
            named = list(node.named_children)
            if len(named) >= 2 and named[0].type == "variable_name":
                var_raw = _node_text(named[0], source).strip()
                if var_raw.startswith("$") and len(var_raw) > 1:
                    try:
                        value = _tree_sitter_to_value(named[1], source)
                        if isinstance(value, PhpArray):
                            out[var_raw[1:]] = value
                    except Exception as exc:
                        if parse_failures is not None:
                            parse_failures.append(
                                {
                                    "category": "extraction_error",
                                    "file_path": file_path or "<unknown>",
                                    "variable": var_raw[1:],
                                    "reason": str(exc),
                                }
                            )
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return out


def sha1_jsonable(value: Any) -> str:
    def _to_jsonable(obj: Any) -> Any:
        if isinstance(obj, PhpArray):
            out: dict[str, Any] = {}
            list_items: list[Any] = []
            for key, item in obj.items:
                if key is None:
                    list_items.append(_to_jsonable(item))
                else:
                    key_str = normalize_string(key) or str(key)
                    out[key_str] = _to_jsonable(item)
            if out and not list_items:
                return out
            if list_items and not out:
                return list_items
            return {"_map": out, "_list": list_items}
        if isinstance(obj, dict):
            return {str(k): _to_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_to_jsonable(v) for v in obj]
        return obj

    encoded = json.dumps(_to_jsonable(value), sort_keys=True, ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha1(encoded).hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def security_source_files(repo_root: Path) -> list[Path]:
    common = repo_root / "app/source/common"
    files: list[Path] = []
    if not common.exists():
        return files
    for path in sorted(common.glob("security*.inc")):
        if path.name == "securityUtil.inc":
            continue
        files.append(path)
    ds_security = common / "ds_security.inc"
    if ds_security.exists() and ds_security not in files:
        files.append(ds_security)
        files.sort()
    return files


def policy_source_files(repo_root: Path) -> list[Path]:
    root = repo_root / "app/source/common/Policies"
    if not root.exists():
        return []
    return sorted(root.rglob("*.pol"))


def menu_source_files(repo_root: Path) -> list[Path]:
    root = repo_root / "app/source/common/Menus"
    if not root.exists():
        return []
    return sorted(root.glob("*.menu"))


def insert_security_operation(
    conn: sqlite3.Connection,
    op_key: str,
    op_numeric_id: int | None,
    source_file: str,
    source_kind: str,
    op_map: dict[str, Any],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO security_operations (
            op_key,
            op_numeric_id,
            title,
            action,
            script,
            force_mode,
            secure_only,
            allow_dev_env_only,
            source_file,
            source_line,
            source_kind,
            raw_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        ON CONFLICT(op_key, op_numeric_id, source_file, source_kind) DO UPDATE SET
            title = excluded.title,
            action = excluded.action,
            script = excluded.script,
            force_mode = excluded.force_mode,
            secure_only = excluded.secure_only,
            allow_dev_env_only = excluded.allow_dev_env_only,
            raw_hash = excluded.raw_hash
        """,
        (
            op_key,
            op_numeric_id,
            normalize_string(op_map.get("title")),
            normalize_string(op_map.get("action")),
            normalize_string(op_map.get("script")),
            normalize_string(op_map.get("force")),
            normalize_bool(op_map.get("secureOnly")),
            normalize_bool(op_map.get("allowDevEnvOnly")),
            source_file,
            source_kind,
            sha1_jsonable(op_map),
        ),
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute(
        """
        SELECT id
        FROM security_operations
        WHERE op_key = ?
          AND IFNULL(op_numeric_id, -1) = IFNULL(?, -1)
          AND source_file = ?
          AND source_kind = ?
        LIMIT 1
        """,
        (op_key, op_numeric_id, source_file, source_kind),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def parse_security_files(
    conn: sqlite3.Connection,
    repo_root: Path,
    stats: BuildStats,
    parse_failures: list[dict[str, Any]],
) -> None:
    files = security_source_files(repo_root)
    stats.files_discovered += len(files)

    for path in tqdm(files, desc="Parsing security*.inc", unit="file"):
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.name == "ds_security.inc":
            include_match = re.search(r"['\"](ds_security_data\\.inc)['\"]", text)
            if include_match:
                data_file = path.parent / include_match.group(1)
                if not data_file.exists():
                    stats.missing_includes += 1
                    parse_failures.append(
                        {
                            "category": "missing_include",
                            "file_path": rel,
                            "reason": f"missing include file: {data_file.relative_to(repo_root).as_posix()}",
                        }
                    )
            stats.files_skipped += 1
            continue

        try:
            k_elements = parse_assigned_array(text, "kElements")
            if not k_elements:
                stats.files_skipped += 1
                continue
        except Exception as exc:
            stats.files_failed += 1
            parse_failures.append(
                {
                    "category": "parse_error",
                    "file_path": rel,
                    "reason": str(exc),
                }
            )
            continue

        stats.files_parsed += 1
        for _, node in k_elements.items:
            op_map = array_to_map(node)
            op_key = normalize_string(op_map.get("key"))
            if not op_key:
                continue
            op_id = normalize_int(op_map.get("id"))
            operation_id = insert_security_operation(
                conn=conn,
                op_key=op_key,
                op_numeric_id=op_id,
                source_file=rel,
                source_kind="security",
                op_map=op_map,
            )
            stats.operations_inserted += 1

            allowops_raw = op_map.get("allowops")
            allowops = [normalize_string(v) for v in array_to_list(allowops_raw)]
            for allowed_op in [value for value in allowops if value]:
                cur_allow = conn.execute(
                    """
                    INSERT OR IGNORE INTO security_operation_allowops (
                        operation_id,
                        allowed_op_key,
                        source_file,
                        source_line
                    )
                    VALUES (?, ?, ?, NULL)
                    """,
                    (operation_id, allowed_op, rel),
                )
                if cur_allow.rowcount > 0:
                    stats.allowops_inserted += 1


def detect_conflicts(
    conn: sqlite3.Connection, conflicts: list[dict[str, Any]], stats: BuildStats
) -> None:
    dup_key_rows = conn.execute(
        """
        SELECT op_key, COUNT(DISTINCT op_numeric_id) AS distinct_ids
        FROM security_operations
        WHERE op_numeric_id IS NOT NULL
        GROUP BY op_key
        HAVING distinct_ids > 1
        """
    ).fetchall()
    for row in dup_key_rows:
        stats.conflicts_detected += 1
        conflicts.append(
            {
                "category": "conflicting_key_to_ids",
                "op_key": row["op_key"],
                "distinct_ids": row["distinct_ids"],
            }
        )

    dup_id_rows = conn.execute(
        """
        SELECT op_numeric_id, COUNT(DISTINCT op_key) AS distinct_keys
        FROM security_operations
        WHERE op_numeric_id IS NOT NULL
        GROUP BY op_numeric_id
        HAVING distinct_keys > 1
        """
    ).fetchall()
    for row in dup_id_rows:
        stats.conflicts_detected += 1
        conflicts.append(
            {
                "category": "conflicting_id_to_keys",
                "op_numeric_id": row["op_numeric_id"],
                "distinct_keys": row["distinct_keys"],
            }
        )


def parse_policy_files(
    conn: sqlite3.Connection,
    repo_root: Path,
    stats: BuildStats,
    parse_failures: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    known_op_keys: set[str],
) -> None:
    files = policy_source_files(repo_root)
    stats.files_discovered += len(files)

    for path in tqdm(files, desc="Parsing Policies/*.pol", unit="file"):
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            policy_root = parse_assigned_array(text, "kPolicy")
            if not policy_root:
                stats.files_skipped += 1
                continue
        except Exception as exc:
            stats.files_failed += 1
            parse_failures.append(
                {
                    "category": "parse_error",
                    "file_path": rel,
                    "reason": str(exc),
                }
            )
            continue

        stats.files_parsed += 1
        module = path.stem
        for policy_name_raw, policy_node in policy_root.items:
            policy_name = normalize_string(policy_name_raw)
            if not policy_name:
                continue
            policy_map = array_to_map(policy_node)
            label = normalize_string(policy_map.get("label"))

            cur = conn.execute(
                """
                INSERT INTO security_policies (
                    policy_name, module, label, source_file, source_line
                )
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(policy_name, source_file) DO UPDATE SET
                    module = excluded.module,
                    label = excluded.label
                """,
                (policy_name, module, label, rel),
            )
            if cur.lastrowid:
                policy_id = int(cur.lastrowid)
            else:
                row = conn.execute(
                    """
                    SELECT id
                    FROM security_policies
                    WHERE policy_name = ? AND source_file = ?
                    LIMIT 1
                    """,
                    (policy_name, rel),
                ).fetchone()
                assert row is not None
                policy_id = int(row["id"])
            stats.policies_inserted += 1

            values_map = array_to_map(policy_map.get("values"))
            for value_key_raw, value_node in values_map.items():
                value_key = normalize_string(value_key_raw)
                if not value_key:
                    continue
                value_map = array_to_map(value_node)
                cur = conn.execute(
                    """
                    INSERT INTO security_policy_values (
                        policy_id, value_key, display, value_label, source_line
                    )
                    VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(policy_id, value_key) DO UPDATE SET
                        display = excluded.display,
                        value_label = excluded.value_label
                    """,
                    (
                        policy_id,
                        value_key,
                        normalize_string(value_map.get("display")),
                        normalize_string(value_map.get("value")),
                    ),
                )
                if cur.lastrowid:
                    value_id = int(cur.lastrowid)
                else:
                    row = conn.execute(
                        """
                        SELECT id
                        FROM security_policy_values
                        WHERE policy_id = ? AND value_key = ?
                        LIMIT 1
                        """,
                        (policy_id, value_key),
                    ).fetchone()
                    assert row is not None
                    value_id = int(row["id"])
                stats.policy_values_inserted += 1

                eops_values = [
                    normalize_string(v) for v in array_to_list(value_map.get("eops"))
                ]
                for op_key in [item for item in eops_values if item]:
                    cur_eop = conn.execute(
                        """
                        INSERT OR IGNORE INTO security_policy_eops (
                            policy_value_id, op_key, source_line
                        )
                        VALUES (?, ?, NULL)
                        """,
                        (value_id, op_key),
                    )
                    if cur_eop.rowcount > 0:
                        stats.policy_eops_inserted += 1
                    if op_key not in known_op_keys:
                        stats.unresolved_policy_keys += 1
                        unresolved.append(
                            {
                                "category": "policy_eop_missing_operation",
                                "file_path": rel,
                                "policy_name": policy_name,
                                "value_key": value_key,
                                "op_key": op_key,
                            }
                        )


def walk_menu_tree(
    node: PhpArray,
    path: list[str],
    callback: Callable[[list[str], dict[str, Any]], None],
) -> None:
    for child_name_raw, child_node in node.items:
        child_name = normalize_string(child_name_raw)
        if child_name is None or child_name.startswith("MENU_"):
            continue
        if not isinstance(child_node, PhpArray):
            continue

        child_map = array_to_map(child_node)
        child_path = path + [child_name]
        callback(child_path, child_map)

        # Standard menu containers.
        for container_key in (
            "MENU_CATEGORY_ITEMS",
            "MENU_SUBMENUS",
            "MENU_POPUPMENUS",
            "category_items",
            "submenus",
            "popupmenus",
        ):
            container = child_map.get(container_key)
            if isinstance(container, PhpArray):
                walk_menu_tree(container, child_path, callback)

        # Direct nested menu sections.
        walk_menu_tree(child_node, child_path, callback)


def parse_menu_files(
    conn: sqlite3.Connection,
    repo_root: Path,
    stats: BuildStats,
    parse_failures: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    op_key_to_operation_id: dict[str, int],
) -> None:
    files = menu_source_files(repo_root)
    stats.files_discovered += len(files)

    for path in tqdm(files, desc="Parsing Menus/*.menu", unit="file"):
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            assigned = parse_all_assigned_arrays(
                text, parse_failures=parse_failures, file_path=rel
            )
        except Exception as exc:
            stats.files_failed += 1
            parse_failures.append(
                {
                    "category": "parse_error",
                    "file_path": rel,
                    "reason": str(exc),
                }
            )
            continue

        menu_var = next((name for name in assigned if name.endswith("_menu")), None)
        if menu_var is None and "menu" in assigned:
            menu_var = "menu"
        if menu_var is None:
            stats.files_skipped += 1
            parse_failures.append(
                {
                    "category": "parse_error",
                    "file_path": rel,
                    "reason": "unable to locate menu root assignment",
                }
            )
            continue

        menu_root = assigned.get(menu_var)
        if not isinstance(menu_root, PhpArray):
            stats.files_skipped += 1
            continue

        module = path.stem.split("_", 1)[0]
        cur = conn.execute(
            """
            INSERT INTO security_menus (module, menu_name, source_file)
            VALUES (?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                module = excluded.module,
                menu_name = excluded.menu_name
            """,
            (module, menu_var, rel),
        )
        if cur.lastrowid:
            menu_id = int(cur.lastrowid)
        else:
            row = conn.execute(
                "SELECT id FROM security_menus WHERE source_file = ? LIMIT 1", (rel,)
            ).fetchone()
            assert row is not None
            menu_id = int(row["id"])
        stats.menus_inserted += 1
        stats.files_parsed += 1

        def on_item(item_path_parts: list[str], item_map: dict[str, Any]) -> None:
            menu_key = normalize_string(item_map.get("MENU_KEY")) or normalize_string(
                item_map.get("key")
            )
            menu_item_id = normalize_string(
                item_map.get("MENU_ID")
            ) or normalize_string(item_map.get("id"))
            menu_script = normalize_string(
                item_map.get("MENU_SCRIPT")
            ) or normalize_string(item_map.get("script"))
            if not any((menu_key, menu_item_id, menu_script)):
                return

            item_path = "/".join(item_path_parts)
            item_name = item_path_parts[-1]
            cur_item = conn.execute(
                """
                INSERT INTO security_menu_items (
                    menu_id, item_path, item_name, menu_item_id, menu_script, menu_key, source_line
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(menu_id, item_path) DO UPDATE SET
                    item_name = excluded.item_name,
                    menu_item_id = excluded.menu_item_id,
                    menu_script = excluded.menu_script,
                    menu_key = excluded.menu_key
                """,
                (menu_id, item_path, item_name, menu_item_id, menu_script, menu_key),
            )
            if cur_item.lastrowid:
                menu_item_row_id = int(cur_item.lastrowid)
            else:
                row = conn.execute(
                    """
                    SELECT id
                    FROM security_menu_items
                    WHERE menu_id = ? AND item_path = ?
                    LIMIT 1
                    """,
                    (menu_id, item_path),
                ).fetchone()
                assert row is not None
                menu_item_row_id = int(row["id"])
            stats.menu_items_inserted += 1

            if not menu_key:
                return
            operation_id = op_key_to_operation_id.get(menu_key)
            reason = "resolved" if operation_id else "missing_operation_key"
            cur_link = conn.execute(
                """
                INSERT OR IGNORE INTO security_menu_op_links (
                    menu_item_id, op_key, operation_id, resolution_reason
                )
                VALUES (?, ?, ?, ?)
                """,
                (menu_item_row_id, menu_key, operation_id, reason),
            )
            if cur_link.rowcount > 0:
                stats.menu_links_inserted += 1
            if operation_id is None:
                stats.unresolved_menu_keys += 1
                unresolved.append(
                    {
                        "category": "menu_key_missing_operation",
                        "file_path": rel,
                        "item_path": item_path,
                        "op_key": menu_key,
                    }
                )

        walk_menu_tree(menu_root, [], on_item)


def parse_dbschema(
    conn: sqlite3.Connection,
    repo_root: Path,
    stats: BuildStats,
    parse_failures: list[dict[str, Any]],
) -> None:
    path = repo_root / "app/source/common/dbschema.inc"
    if not path.exists():
        stats.files_skipped += 1
        parse_failures.append(
            {
                "category": "missing_include",
                "file_path": "app/source/common/dbschema.inc",
                "reason": "dbschema.inc not found",
            }
        )
        return
    stats.files_discovered += 1
    rel = path.relative_to(repo_root).as_posix()
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        k_tables = parse_assigned_array(text, "kTables")
    except Exception as exc:
        stats.files_failed += 1
        parse_failures.append(
            {
                "category": "parse_error",
                "file_path": rel,
                "reason": str(exc),
            }
        )
        return

    if not k_tables:
        stats.files_skipped += 1
        parse_failures.append(
            {
                "category": "unsupported_construct",
                "file_path": rel,
                "reason": "kTables assignment not parseable",
            }
        )
        return

    stats.files_parsed += 1
    for table_name_raw, table_node in k_tables.items:
        table_name = normalize_string(table_name_raw)
        if not table_name:
            continue
        table_map = array_to_map(table_node)
        pkeys = [
            normalize_string(v) for v in array_to_list(table_map.get("primarykey"))
        ]
        primary_keys = ",".join([v for v in pkeys if v]) if any(pkeys) else None

        cur = conn.execute(
            """
            INSERT INTO dbschema_tables (
                table_name, primary_keys, source_file, source_line, raw_hash
            )
            VALUES (?, ?, ?, NULL, ?)
            ON CONFLICT(table_name, source_file) DO UPDATE SET
                primary_keys = excluded.primary_keys,
                raw_hash = excluded.raw_hash
            """,
            (table_name, primary_keys, rel, sha1_jsonable(table_map)),
        )
        if cur.lastrowid:
            table_id = int(cur.lastrowid)
        else:
            row = conn.execute(
                """
                SELECT id
                FROM dbschema_tables
                WHERE table_name = ? AND source_file = ?
                LIMIT 1
                """,
                (table_name, rel),
            ).fetchone()
            assert row is not None
            table_id = int(row["id"])
        stats.dbschema_tables_inserted += 1

        field_info = array_to_map(table_map.get("db_fieldinfo"))
        for field_name_raw, field_node in field_info.items():
            field_name = normalize_string(field_name_raw)
            if not field_name:
                continue
            field_map = array_to_map(field_node)
            conn.execute(
                """
                INSERT OR REPLACE INTO dbschema_fields (
                    dbschema_table_id, field_name, field_type, source_line
                )
                VALUES (?, ?, ?, NULL)
                """,
                (table_id, field_name, normalize_string(field_map.get("type"))),
            )
            stats.dbschema_fields_inserted += 1


def load_operation_key_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT op_key, MIN(id) AS operation_id
        FROM security_operations
        GROUP BY op_key
        """
    ).fetchall()
    return {str(row["op_key"]): int(row["operation_id"]) for row in rows}


def build(
    db: str,
    repo_root: Path,
    reset: bool,
    max_parse_failures: int,
    max_unresolved: int,
) -> BuildStats:
    conn = get_connection(db)
    parse_failures: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    stats = BuildStats()

    try:
        ensure_security_tables(conn)
        if reset:
            reset_security_tables(conn)

        parse_security_files(conn, repo_root, stats, parse_failures)
        detect_conflicts(conn, conflicts, stats)
        op_key_map = load_operation_key_map(conn)
        parse_policy_files(
            conn=conn,
            repo_root=repo_root,
            stats=stats,
            parse_failures=parse_failures,
            unresolved=unresolved,
            known_op_keys=set(op_key_map.keys()),
        )
        parse_menu_files(
            conn=conn,
            repo_root=repo_root,
            stats=stats,
            parse_failures=parse_failures,
            unresolved=unresolved,
            op_key_to_operation_id=op_key_map,
        )
        parse_dbschema(conn, repo_root, stats, parse_failures)
        conn.commit()
    finally:
        conn.close()

    write_jsonl(PARSE_FAILURES_LOG, parse_failures)
    write_jsonl(UNRESOLVED_LOG, unresolved)
    write_jsonl(CONFLICTS_LOG, conflicts)

    if max_parse_failures >= 0 and len(parse_failures) > max_parse_failures:
        raise click.ClickException(
            f"parse failures {len(parse_failures)} exceeded threshold {max_parse_failures}"
        )
    if max_unresolved >= 0 and stats.unresolved_total > max_unresolved:
        raise click.ClickException(
            f"unresolved keys {stats.unresolved_total} exceeded threshold {max_unresolved}"
        )

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option(
    "--db", default=DEFAULT_DB, show_default=True, help="Catalog database path."
)
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=Path(DEFAULT_REPO_ROOT),
    show_default=True,
    help="Intacct source repository root.",
)
@click.option(
    "--reset/--no-reset",
    default=True,
    show_default=True,
    help="Rebuild security mapping tables from a clean snapshot.",
)
@click.option(
    "--max-parse-failures",
    default=-1,
    show_default=True,
    help="Fail if parse failure count exceeds this threshold (-1 disables).",
)
@click.option(
    "--max-unresolved",
    default=-1,
    show_default=True,
    help="Fail if unresolved menu/policy op keys exceed this threshold (-1 disables).",
)
def build_command(
    db: str,
    repo_root: Path,
    reset: bool,
    max_parse_failures: int,
    max_unresolved: int,
) -> None:
    stats = build(
        db=db,
        repo_root=repo_root.resolve(),
        reset=reset,
        max_parse_failures=max_parse_failures,
        max_unresolved=max_unresolved,
    )
    click.echo(f"Files discovered:             {stats.files_discovered}")
    click.echo(f"Files parsed:                 {stats.files_parsed}")
    click.echo(f"Files skipped:                {stats.files_skipped}")
    click.echo(f"Files failed:                 {stats.files_failed}")
    click.echo(f"Security operations:          {stats.operations_inserted}")
    click.echo(f"Security allowops links:      {stats.allowops_inserted}")
    click.echo(f"Policies:                     {stats.policies_inserted}")
    click.echo(f"Policy values:                {stats.policy_values_inserted}")
    click.echo(f"Policy eops:                  {stats.policy_eops_inserted}")
    click.echo(f"Menus:                        {stats.menus_inserted}")
    click.echo(f"Menu items:                   {stats.menu_items_inserted}")
    click.echo(f"Menu op links:                {stats.menu_links_inserted}")
    click.echo(f"DB schema tables:             {stats.dbschema_tables_inserted}")
    click.echo(f"DB schema fields:             {stats.dbschema_fields_inserted}")
    click.echo(f"Unresolved policy keys:       {stats.unresolved_policy_keys}")
    click.echo(f"Unresolved menu keys:         {stats.unresolved_menu_keys}")
    click.echo(f"Mapping conflicts detected:   {stats.conflicts_detected}")
    click.echo(f"Missing include references:   {stats.missing_includes}")
    click.echo(f"Parse failure log:            {PARSE_FAILURES_LOG.as_posix()}")
    click.echo(f"Unresolved key log:           {UNRESOLVED_LOG.as_posix()}")
    click.echo(f"Conflict log:                 {CONFLICTS_LOG.as_posix()}")


if __name__ == "__main__":
    cli()
