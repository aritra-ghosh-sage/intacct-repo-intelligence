"""Conservative Tree-sitter extraction of repo-v1 security facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter_languages import get_parser

from catalog.source_snapshot import SourceSnapshot

EXTRACTOR = "repo_v1_security"
EXTRACTOR_VERSION = "1"
_PARSER = get_parser("php")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DYNAMIC = object()


@dataclass(frozen=True)
class SecurityStats:
    operation_count: int
    allowop_count: int
    policy_count: int
    policy_value_count: int
    policy_eop_count: int
    menu_count: int
    menu_item_count: int
    menu_op_link_count: int
    diagnostic_count: int
    unresolved_link_count: int
    conflict_count: int


@dataclass(frozen=True)
class _Value:
    value: object
    node: object


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line(node) -> tuple[int, int]:
    return node.start_point[0] + 1, max(node.start_point[0] + 1, node.end_point[0] + 1)


def _string(node, source: bytes) -> str | None:
    if node.type != "string":
        return None
    value = _text(node, source)
    if len(value) < 2 or value[0] not in "'\"" or value[-1] != value[0]:
        return None
    return value[1:-1]


def _value(node, source: bytes) -> object:
    string = _string(node, source)
    if string is not None:
        return string
    if node.type == "integer":
        try:
            return int(_text(node, source), 10)
        except ValueError:
            return _DYNAMIC
    if node.type == "boolean":
        return _text(node, source).lower() == "true"
    if node.type == "name" and _text(node, source).lower() == "null":
        return None
    if node.type == "array_creation_expression":
        items: list[tuple[object | None, _Value]] = []
        ordinal = 0
        for child in node.named_children:
            if child.type != "array_element_initializer":
                continue
            parts = list(child.named_children)
            if len(parts) == 1:
                key, value_node = None, parts[0]
            elif len(parts) >= 2:
                key, value_node = _value(parts[0], source), parts[-1]
            else:
                continue
            if key is _DYNAMIC:
                key = None
            items.append(
                (
                    key if key is not None else ordinal,
                    _Value(_value(value_node, source), value_node),
                )
            )
            ordinal += 1
        return items
    return _DYNAMIC


def _assignments(root, source: bytes) -> dict[str, _Value]:
    result: dict[str, _Value] = {}

    def visit(node) -> None:
        if node.type == "assignment_expression":
            parts = list(node.named_children)
            if len(parts) == 2 and parts[0].type == "variable_name":
                result[_text(parts[0], source).lstrip("$")] = _Value(
                    _value(parts[1], source), parts[1]
                )
        for child in node.named_children:
            visit(child)

    visit(root)
    return result


def _array(value: object) -> list[tuple[object, _Value]]:
    return value if isinstance(value, list) else []


def _map(value: object) -> dict[str, _Value]:
    return {str(k): v for k, v in _array(value) if isinstance(k, str)}


def _scalar(value: _Value | None) -> object:
    return None if value is None or value.value is _DYNAMIC else value.value


def _dynamic(value: _Value | None) -> bool:
    return value is not None and value.value is _DYNAMIC


def _required(value: _Value | None) -> str | int | None:
    raw = _scalar(value)
    return (
        raw
        if isinstance(raw, (str, int)) and (not isinstance(raw, str) or raw != "")
        else None
    )


def _fact_key(kind: str, path: str, pointer: str, occurrence: object = None) -> str:
    return hashlib.sha256(
        _canonical(
            {"kind": kind, "path": path, "pointer": pointer, "occurrence": occurrence}
        ).encode()
    ).hexdigest()


def _source_meta(
    conn, repo_id: int, snapshot: SourceSnapshot, path: str, node
) -> tuple[int, str, str, int, int]:
    row = conn.execute(
        "SELECT id,source_commit_sha FROM files WHERE repo_id=? AND path=?",
        (repo_id, path),
    ).fetchone()
    if row is None or str(row[1]) != snapshot.target_sha:
        raise RuntimeError(f"security source is not in candidate: {path}")
    raw = (snapshot.snapshot_root / PurePosixPath(path)).read_bytes()
    start, end = _line(node)
    return int(row[0]), hashlib.sha256(raw).hexdigest(), str(row[1]), start, end


def _insert_fact(conn, table: str, fields: dict[str, object]) -> int:
    names = list(fields)
    cur = conn.execute(
        f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",
        tuple(fields[n] for n in names),
    )
    return int(cur.lastrowid)


def _evidence(kind: str, fields: dict[str, object]) -> str:
    return _canonical({"fact_type": kind, "fields": fields})


def _diag(
    conn,
    repo_id,
    file_id,
    path,
    raw_hash,
    sha,
    start,
    end,
    code,
    message,
    pointer,
    detail,
):
    evidence = _canonical(
        {
            "fact_type": "security.diagnostic",
            "code": code,
            "message": message,
            "source_path": path,
            "source_pointer": pointer,
            "detail": detail,
        }
    )
    key = _fact_key("diagnostic:" + code, path, pointer, detail)
    conn.execute(
        """INSERT OR IGNORE INTO security_diagnostics(repo_id,file_id,subject_kind,subject_key,diagnostic_key,severity,code,message,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            repo_id,
            file_id,
            None,
            None,
            key,
            "warning",
            code,
            message,
            file_id,
            path,
            sha,
            raw_hash,
            pointer,
            start,
            end,
            evidence,
            EXTRACTOR,
            EXTRACTOR_VERSION,
        ),
    )


def _files(snapshot: SourceSnapshot) -> list[str]:
    paths = []
    for entry in snapshot.entries:
        path = entry.path
        if (
            (
                path.startswith("app/source/common/security")
                and path.endswith(".inc")
                and PurePosixPath(path).name
                not in {"securityUtil.inc", "ds_security_data.inc"}
            )
            or path == "app/source/common/ds_security.inc"
            or (
                path.startswith("app/source/common/Policies/") and path.endswith(".pol")
            )
            or (path.startswith("app/source/common/Menus/") and path.endswith(".menu"))
        ):
            paths.append(path)
    return sorted(set(paths))


def extract_snapshot_security(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> SecurityStats:
    del show_progress
    for table in (
        "security_menu_op_links",
        "security_policy_eops",
        "security_operation_allowops",
        "security_menu_items",
        "security_policy_values",
        "security_menus",
        "security_policies",
        "security_operations",
        "security_diagnostics",
    ):
        conn.execute(f"DELETE FROM {table} WHERE repo_id=?", (repo_id,))
    operations: list[tuple[int, str | None, int | None]] = []
    pending_allow: list[tuple[int, str, dict]] = []
    pending_eops: list[tuple[int, str, dict]] = []
    pending_menu: list[tuple[int, str, dict]] = []
    counts = {
        "operation": 0,
        "allow": 0,
        "policy": 0,
        "value": 0,
        "eop": 0,
        "menu": 0,
        "item": 0,
        "link": 0,
        "diag": 0,
        "unresolved": 0,
        "conflict": 0,
    }
    for path in _files(snapshot):
        raw = (snapshot.snapshot_root / PurePosixPath(path)).read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        lines = raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)
        tree = _PARSER.parse(raw)
        file_row = conn.execute(
            "SELECT id FROM files WHERE repo_id=? AND path=?", (repo_id, path)
        ).fetchone()
        if file_row is None:
            raise RuntimeError(f"security file missing from candidate: {path}")
        file_id = int(file_row[0])
        if tree.root_node.has_error:
            counts["diag"] += 1
            _diag(
                conn,
                repo_id,
                file_id,
                path,
                source_hash,
                snapshot.target_sha,
                1,
                lines,
                "security.php.parse_error",
                "Tree-sitter reported a PHP syntax error",
                "/",
                {"path": path},
            )
            continue
        if path.endswith("ds_security.inc"):
            if b"ds_security_data.inc" in raw and not any(
                e.path == "app/source/common/ds_security_data.inc"
                for e in snapshot.entries
            ):
                counts["diag"] += 1
                _diag(
                    conn,
                    repo_id,
                    file_id,
                    path,
                    source_hash,
                    snapshot.target_sha,
                    1,
                    lines,
                    "security.include.missing",
                    "retained ds_security_data.inc include is missing",
                    "/",
                    {"include": "app/source/common/ds_security_data.inc"},
                )
            continue
        assignments = _assignments(tree.root_node, raw)
        if path.endswith(".pol"):
            root = assignments.get("kPolicy")
            if root is not None and not _array(root.value):
                counts["diag"] += 1
                _diag(
                    conn,
                    repo_id,
                    file_id,
                    path,
                    source_hash,
                    snapshot.target_sha,
                    *_line(root.node),
                    "security.php.assignment_missing",
                    "kPolicy assignment is not a literal array",
                    "/kPolicy",
                    {"assignment": "kPolicy"},
                )
                continue
            if root is None:
                continue
            for ordinal, (policy_key, policy_value) in enumerate(_array(root.value)):
                pname = _required(_Value(policy_key, policy_value.node))
                if not isinstance(pname, str):
                    counts["diag"] += 1
                    _diag(
                        conn,
                        repo_id,
                        file_id,
                        path,
                        source_hash,
                        snapshot.target_sha,
                        *_line(root.node),
                        "security.value.dynamic",
                        "dynamic policy identity was omitted",
                        f"/kPolicy/{ordinal}",
                        {"kind": "policy"},
                    )
                    continue
                pointer = f"/kPolicy/{ordinal}"
                fields = {
                    "policy_name": pname,
                    "module": PurePosixPath(path).stem,
                    "label": _scalar(_map(policy_value.value).get("label")),
                }
                fields.update(source_path=path, source_pointer=pointer)
                pid = _insert_fact(
                    conn,
                    "security_policies",
                    {
                        "repo_id": repo_id,
                        "fact_key": _fact_key("policy", path, pointer),
                        **fields,
                        "source_file_id": file_id,
                        "source_commit_sha": snapshot.target_sha,
                        "source_hash": source_hash,
                        "start_line": _line(policy_value.node)[0],
                        "end_line": _line(policy_value.node)[1],
                        "evidence": _evidence("policy", fields),
                        "extractor": EXTRACTOR,
                        "extractor_version": EXTRACTOR_VERSION,
                    },
                )
                counts["policy"] += 1
                values_value = _map(policy_value.value).get("values")
                values = _array(values_value.value if values_value else [])
                for vord, (raw_vkey, vval) in enumerate(values):
                    vkey = str(raw_vkey)
                    vmap = _map(vval.value)
                    pointer2 = f"{pointer}/values/{vord}"
                    vf = {
                        "value_key": vkey,
                        "display": _scalar(vmap.get("display")),
                        "value_label": _scalar(vmap.get("value")),
                        "source_path": path,
                        "source_pointer": pointer2,
                    }
                    vid = _insert_fact(
                        conn,
                        "security_policy_values",
                        {
                            "repo_id": repo_id,
                            "fact_key": _fact_key("policy-value", path, pointer2),
                            "policy_id": pid,
                            **vf,
                            "source_file_id": file_id,
                            "source_commit_sha": snapshot.target_sha,
                            "source_hash": source_hash,
                            "start_line": _line(vval.node)[0],
                            "end_line": _line(vval.node)[1],
                            "evidence": _evidence("policy-value", vf),
                            "extractor": EXTRACTOR,
                            "extractor_version": EXTRACTOR_VERSION,
                        },
                    )
                    counts["value"] += 1
                    for eord, eop in enumerate(
                        _array(vmap.get("eops").value if vmap.get("eops") else [])
                    ):
                        op = _required(eop[1])
                        if isinstance(op, str):
                            pending_eops.append(
                                (
                                    vid,
                                    op,
                                    {
                                        "path": path,
                                        "hash": source_hash,
                                        "pointer": f"{pointer2}/eops/{eord}",
                                        "node": eop[1].node,
                                        "file_id": file_id,
                                    },
                                )
                            )
                        elif _dynamic(eop[1]):
                            counts["diag"] += 1
                            _diag(
                                conn,
                                repo_id,
                                file_id,
                                path,
                                source_hash,
                                snapshot.target_sha,
                                *_line(eop[1].node),
                                "security.value.dynamic",
                                "dynamic policy operation reference was omitted",
                                f"{pointer2}/eops/{eord}",
                                {"kind": "policy_eop"},
                            )
        elif path.endswith(".menu"):
            root_name, root = next(
                (
                    (n, v)
                    for n, v in assignments.items()
                    if n == "menu" or n.endswith("_menu")
                ),
                (None, None),
            )
            if root is not None and not _array(root.value):
                counts["diag"] += 1
                _diag(
                    conn,
                    repo_id,
                    file_id,
                    path,
                    source_hash,
                    snapshot.target_sha,
                    *_line(root.node),
                    "security.php.assignment_missing",
                    "menu assignment is not a literal array",
                    f"/{root_name}",
                    {"assignment": root_name},
                )
                continue
            if root is None:
                continue
            pointer = "/" + str(root_name)
            mf = {
                "module": PurePosixPath(path).stem.split("_", 1)[0],
                "menu_name": root_name,
                "source_path": path,
                "source_pointer": pointer,
            }
            mid = _insert_fact(
                conn,
                "security_menus",
                {
                    "repo_id": repo_id,
                    "fact_key": _fact_key("menu", path, pointer),
                    **mf,
                    "source_file_id": file_id,
                    "source_commit_sha": snapshot.target_sha,
                    "source_hash": source_hash,
                    "start_line": _line(root.node)[0],
                    "end_line": _line(root.node)[1],
                    "evidence": _evidence("menu", mf),
                    "extractor": EXTRACTOR,
                    "extractor_version": EXTRACTOR_VERSION,
                },
            )
            counts["menu"] += 1

            def walk(
                value,
                indexes,
                root_pointer=pointer,
                path_value=path,
                menu_id=mid,
                source_file_id=file_id,
                source_hash_value=source_hash,
            ):
                for ord_, (key, item) in enumerate(_array(value)):
                    itemmap = _map(item.value)
                    new = indexes + [ord_]
                    ip = "/".join(str(x) for x in new)
                    mk = _scalar(itemmap.get("MENU_KEY")) or _scalar(itemmap.get("key"))
                    mi = _scalar(itemmap.get("MENU_ID")) or _scalar(itemmap.get("id"))
                    ms = _scalar(itemmap.get("MENU_SCRIPT")) or _scalar(
                        itemmap.get("script")
                    )
                    dynamic_menu_key = _dynamic(itemmap.get("MENU_KEY")) or _dynamic(
                        itemmap.get("key")
                    )
                    if dynamic_menu_key:
                        counts["diag"] += 1
                        _diag(
                            conn,
                            repo_id,
                            source_file_id,
                            path_value,
                            source_hash_value,
                            snapshot.target_sha,
                            *_line(item.node),
                            "security.value.dynamic",
                            "dynamic menu operation reference was omitted",
                            f"{root_pointer}/" + "/".join(str(x) for x in new),
                            {"kind": "menu_key"},
                        )
                    if not any(x is not None for x in (mk, mi, ms)):
                        walk(item.value, new, root_pointer)
                        continue
                    pointer2 = f"{root_pointer}/" + "/".join(str(x) for x in new)
                    itemf = {
                        "item_path": ip,
                        "item_name": str(key),
                        "menu_item_id": mi,
                        "menu_script": ms,
                        "menu_key": mk,
                        "source_path": path_value,
                        "source_pointer": pointer2,
                    }
                    iid = _insert_fact(
                        conn,
                        "security_menu_items",
                        {
                            "repo_id": repo_id,
                            "fact_key": _fact_key("menu-item", path_value, pointer2),
                            "menu_id": menu_id,
                            **itemf,
                            "source_file_id": source_file_id,
                            "source_commit_sha": snapshot.target_sha,
                            "source_hash": source_hash_value,
                            "start_line": _line(item.node)[0],
                            "end_line": _line(item.node)[1],
                            "evidence": _evidence("menu-item", itemf),
                            "extractor": EXTRACTOR,
                            "extractor_version": EXTRACTOR_VERSION,
                        },
                    )
                    counts["item"] += 1
                    if isinstance(mk, str):
                        pending_menu.append(
                            (
                                iid,
                                mk,
                                {
                                    "path": path_value,
                                    "hash": source_hash_value,
                                    "pointer": pointer2,
                                    "node": item.node,
                                    "file_id": source_file_id,
                                },
                            )
                        )
                    walk(item.value, new, root_pointer)

            walk(root.value, [])
        else:
            root = assignments.get("kElements")
            if root is not None and not _array(root.value):
                counts["diag"] += 1
                _diag(
                    conn,
                    repo_id,
                    file_id,
                    path,
                    source_hash,
                    snapshot.target_sha,
                    *_line(root.node),
                    "security.php.assignment_missing",
                    "kElements assignment is not a literal array",
                    "/kElements",
                    {"assignment": "kElements"},
                )
                continue
            if root is None:
                continue
            for ordinal, (_, item) in enumerate(_array(root.value)):
                omap = _map(item.value)
                op = _required(omap.get("key"))
                if not isinstance(op, str):
                    counts["diag"] += 1
                    _diag(
                        conn,
                        repo_id,
                        file_id,
                        path,
                        source_hash,
                        snapshot.target_sha,
                        *_line(item.node),
                        "security.value.dynamic",
                        "dynamic operation identity was omitted",
                        f"/kElements/{ordinal}",
                        {"kind": "operation"},
                    )
                    continue
                oid = _scalar(omap.get("id"))
                oid = oid if isinstance(oid, int) else None
                pointer = f"/kElements/{ordinal}"
                of = {
                    "op_key": op,
                    "op_numeric_id": oid,
                    "title": _scalar(omap.get("title")),
                    "action": _scalar(omap.get("action")),
                    "script": _scalar(omap.get("script")),
                    "force_mode": _scalar(omap.get("force")),
                    "secure_only": _scalar(omap.get("secureOnly")),
                    "allow_dev_env_only": _scalar(omap.get("allowDevEnvOnly")),
                    "source_path": path,
                    "source_pointer": pointer,
                }
                opid = _insert_fact(
                    conn,
                    "security_operations",
                    {
                        "repo_id": repo_id,
                        "fact_key": _fact_key("operation", path, pointer),
                        **of,
                        "source_file_id": file_id,
                        "source_commit_sha": snapshot.target_sha,
                        "source_hash": source_hash,
                        "start_line": _line(item.node)[0],
                        "end_line": _line(item.node)[1],
                        "evidence": _evidence("operation", of),
                        "extractor": EXTRACTOR,
                        "extractor_version": EXTRACTOR_VERSION,
                    },
                )
                operations.append((opid, op, oid))
                counts["operation"] += 1
                for aord, (ak, av) in enumerate(
                    _array(omap.get("allowops").value if omap.get("allowops") else [])
                ):
                    aval = _scalar(av)
                    if _dynamic(av):
                        counts["diag"] += 1
                        _diag(
                            conn,
                            repo_id,
                            file_id,
                            path,
                            source_hash,
                            snapshot.target_sha,
                            *_line(av.node),
                            "security.value.dynamic",
                            "dynamic allow-operation reference was omitted",
                            f"{pointer}/allowops/{aord}",
                            {"kind": "allowop"},
                        )
                        continue
                    pending_allow.append(
                        (
                            opid,
                            str(aval) if aval is not None else "",
                            {
                                "path": path,
                                "hash": source_hash,
                                "pointer": f"{pointer}/allowops/{aord}",
                                "node": av.node,
                                "file_id": file_id,
                            },
                        )
                    )
                    counts["allow"] += 1
    _resolve_refs(
        conn,
        repo_id,
        pending_allow,
        pending_eops,
        pending_menu,
        counts,
        snapshot.target_sha,
    )
    conflicts = 0
    for row in conn.execute(
        "SELECT op_key FROM security_operations WHERE repo_id=? GROUP BY op_key",
        (repo_id,),
    ):
        definitions = conn.execute(
            "SELECT fact_key,op_numeric_id FROM security_operations WHERE repo_id=? AND op_key=? ORDER BY id",
            (repo_id, row[0]),
        ).fetchall()
        identities = {(r[1] if r[1] is not None else None) for r in definitions}
        if len(identities) <= 1:
            continue
        conflicts += 1
        source = conn.execute(
            "SELECT source_file_id,source_path,source_hash,start_line,end_line FROM security_operations WHERE repo_id=? AND op_key=? ORDER BY id LIMIT 1",
            (repo_id, row[0]),
        ).fetchone()
        _diag(
            conn,
            repo_id,
            int(source[0]),
            str(source[1]),
            str(source[2]),
            snapshot.target_sha,
            int(source[3]),
            int(source[4]),
            "security.operation.conflict",
            "operation key has conflicting numeric identities",
            "/",
            {
                "op_key": row[0],
                "definitions": [
                    {"fact_key": r[0], "op_numeric_id": r[1]} for r in definitions
                ],
            },
        )
    counts["conflict"] = conflicts
    counts["diag"] += conflicts
    return SecurityStats(
        counts["operation"],
        counts["allow"],
        counts["policy"],
        counts["value"],
        counts["eop"],
        counts["menu"],
        counts["item"],
        counts["link"],
        counts["diag"],
        counts["unresolved"],
        conflicts,
    )


def _resolve_refs(conn, repo_id, allows, eops, menus, counts, sha):
    def resolve(kind, parent, raw, meta, table, target_col, code):
        if kind == "allow":
            matches = (
                conn.execute(
                    "SELECT id FROM security_operations WHERE repo_id=? AND op_numeric_id=?",
                    (repo_id, int(raw) if re.fullmatch(r"-?\d+", raw) else None),
                ).fetchall()
                if re.fullmatch(r"-?\d+", raw)
                else []
            )
        else:
            matches = conn.execute(
                "SELECT id FROM security_operations WHERE repo_id=? AND op_key=?",
                (repo_id, raw),
            ).fetchall()
        status = "resolved" if len(matches) == 1 else "unresolved"
        reason = (
            ("unique_numeric_id" if kind == "allow" else "unique_operation_key")
            if status == "resolved"
            else (
                (
                    "ambiguous_numeric_operation"
                    if kind == "allow"
                    else "ambiguous_operation_key"
                )
                if matches
                else (
                    "missing_numeric_operation"
                    if kind == "allow"
                    else "missing_operation_key"
                )
            )
        )
        target = int(matches[0][0]) if status == "resolved" else None
        if status == "unresolved":
            counts["unresolved"] += 1
            counts["diag"] += 1
            _diag(
                conn,
                repo_id,
                meta["file_id"],
                meta["path"],
                meta["hash"],
                sha,
                *_line(meta["node"]),
                code,
                reason,
                meta["pointer"],
                {"value": raw},
            )
        fields = {"source_path": meta["path"], "source_pointer": meta["pointer"]}
        if kind == "allow":
            evidence_fields = {
                "allowed_op_key": raw,
                "resolution_status": status,
                "resolution_reason": reason,
                "allowed_operation_id": target,
                **fields,
            }
        else:
            evidence_fields = {
                "op_key": raw,
                "resolution_status": status,
                "resolution_reason": reason,
                "operation_id": target,
                **fields,
            }
        vals = {
            "repo_id": repo_id,
            "fact_key": _fact_key(kind, meta["path"], meta["pointer"]),
            "source_file_id": meta["file_id"],
            "source_path": meta["path"],
            "source_commit_sha": sha,
            "source_hash": meta["hash"],
            "source_pointer": meta["pointer"],
            "start_line": _line(meta["node"])[0],
            "end_line": _line(meta["node"])[1],
            "evidence": _evidence(kind, evidence_fields),
            "extractor": EXTRACTOR,
            "extractor_version": EXTRACTOR_VERSION,
            "resolution_status": status,
            "resolution_reason": reason,
        }
        if kind == "allow":
            vals.update(
                operation_id=parent, allowed_op_key=raw, allowed_operation_id=target
            )
            tab = "security_operation_allowops"
        elif kind == "eop":
            vals.update(policy_value_id=parent, op_key=raw, operation_id=target)
            tab = "security_policy_eops"
        else:
            vals.update(menu_item_id=parent, op_key=raw, operation_id=target)
            tab = "security_menu_op_links"
        _insert_fact(conn, tab, vals)
        if kind == "eop":
            counts["eop"] += 1
        elif kind == "menu":
            counts["link"] += 1

    for parent, raw, meta in allows:
        resolve("allow", parent, raw, meta, "", "", "security.allowop.unresolved")
    for parent, raw, meta in eops:
        resolve("eop", parent, raw, meta, "", "", "security.policy_eop.unresolved")
    for parent, raw, meta in menus:
        resolve("menu", parent, raw, meta, "", "", "security.menu_op.unresolved")


def validate_security_candidate(
    conn: sqlite3.Connection, *, repo_id: int, target_commit_sha: str
) -> None:
    tables = (
        "security_operations",
        "security_operation_allowops",
        "security_policies",
        "security_policy_values",
        "security_policy_eops",
        "security_menus",
        "security_menu_items",
        "security_menu_op_links",
        "security_diagnostics",
    )
    kinds = {
        "security_operations": "operation",
        "security_operation_allowops": "allow",
        "security_policies": "policy",
        "security_policy_values": "policy-value",
        "security_policy_eops": "eop",
        "security_menus": "menu",
        "security_menu_items": "menu-item",
        "security_menu_op_links": "menu",
    }
    for table in tables:
        for row in conn.execute(f"SELECT * FROM {table} WHERE repo_id=?", (repo_id,)):
            if (
                row["source_commit_sha"] != target_commit_sha
                or not _SHA256.fullmatch(str(row["source_hash"]))
                or not row["evidence"]
                or row["extractor"] != EXTRACTOR
                or row["extractor_version"] != EXTRACTOR_VERSION
                or int(row["start_line"]) < 1
                or int(row["end_line"]) < int(row["start_line"])
            ):
                raise RuntimeError(f"security provenance validation failed: {table}")
            file_row = conn.execute(
                "SELECT repo_id,path,source_commit_sha FROM files WHERE id=?",
                (row["source_file_id"],),
            ).fetchone()
            if (
                file_row is None
                or file_row[0] != repo_id
                or file_row[1] != row["source_path"]
                or file_row[2] != target_commit_sha
            ):
                raise RuntimeError(
                    f"security source ownership validation failed: {table}"
                )
            if table in kinds and row["fact_key"] != _fact_key(
                kinds[table], row["source_path"], row["source_pointer"]
            ):
                raise RuntimeError(f"security fact key validation failed: {table}")
            if table == "security_diagnostics" and (
                row["severity"] != "warning"
                or row["code"]
                not in {
                    "security.php.parse_error",
                    "security.php.assignment_missing",
                    "security.value.dynamic",
                    "security.include.missing",
                    "security.operation.conflict",
                    "security.allowop.unresolved",
                    "security.policy_eop.unresolved",
                    "security.menu_op.unresolved",
                }
            ):
                raise RuntimeError("security diagnostic validation failed")
            try:
                evidence = json.loads(str(row["evidence"]))
            except ValueError as exc:
                raise RuntimeError(f"security evidence is invalid: {table}") from exc
            if _canonical(evidence) != str(row["evidence"]):
                raise RuntimeError(f"security evidence is not canonical: {table}")
            fields = evidence.get("fields") if isinstance(evidence, dict) else None
            if isinstance(fields, dict):
                for name in (
                    "op_key",
                    "op_numeric_id",
                    "title",
                    "action",
                    "script",
                    "force_mode",
                    "secure_only",
                    "allow_dev_env_only",
                    "policy_name",
                    "module",
                    "label",
                    "value_key",
                    "display",
                    "value_label",
                    "item_path",
                    "item_name",
                    "menu_item_id",
                    "menu_script",
                    "menu_key",
                    "menu_name",
                    "menu_key",
                    "allowed_op_key",
                    "resolution_status",
                    "resolution_reason",
                    "allowed_operation_id",
                    "operation_id",
                ):
                    if name in fields and fields[name] != row[name]:
                        raise RuntimeError(
                            f"security evidence scalar mismatch: {table}.{name}"
                        )
            if table in {
                "security_operation_allowops",
                "security_policy_eops",
                "security_menu_op_links",
            } and (
                (row["resolution_status"] == "resolved")
                != (
                    row["operation_id"] is not None
                    if table != "security_operation_allowops"
                    else row["allowed_operation_id"] is not None
                )
            ):
                raise RuntimeError(f"security resolution state invalid: {table}")
