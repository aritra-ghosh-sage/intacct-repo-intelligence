"""Verify catalog eval payloads against the current SQLite-backed query output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _db_cli_arg(db: Path) -> str:
    resolved = db.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def query_command(case: dict[str, Any], db: Path) -> list[str]:
    query = case["query"]
    command = query["command"]
    args = query.get("args", {})
    db_arg = _db_cli_arg(db)
    if command in {"stats", "toplevel"}:
        return [sys.executable, "scripts/query_catalog.py", command, "--json"]
    if command == "find":
        return [
            sys.executable,
            "scripts/query_catalog.py",
            "find",
            args["keyword"],
            "--limit",
            str(args["limit"]),
            "--json",
        ]
    if command == "symbols":
        result = [
            sys.executable,
            "scripts/query_catalog.py",
            "symbols",
            args["keyword"],
        ]
        if args.get("kind"):
            result.extend(["--kind", args["kind"]])
        return result + ["--limit", str(args["limit"]), "--json"]
    if command == "entity":
        return [
            sys.executable,
            "scripts/query_entity.py",
            "entity",
            args["entity_name"],
            "--db",
            db_arg,
            "--json",
        ]
    if command == "coverage":
        return [
            sys.executable,
            "scripts/query_rest.py",
            "coverage",
            args["entity_name"],
            "--db",
            db_arg,
            "--json",
        ]
    raise ValueError(f"unsupported reproducible query command: {command}")


def _query_env(db: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CATALOG_DB"] = str(db.resolve())
    return env


def run_query(case: dict[str, Any], db: Path) -> dict[str, Any]:
    completed = subprocess.run(
        query_command(case, db),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_query_env(db),
    )
    if completed.returncode:
        raise RuntimeError(
            f"query failed for {case['case_id']}: {completed.stderr.strip()}"
        )
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "evals/catalog_eval_cases.jsonl"
    )
    parser.add_argument("--db", type=Path, default=ROOT / "catalog/catalog.db")
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "evals/catalog_eval_provenance.jsonl"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Fail when a live query differs from a stored non-synthetic payload.",
    )
    parser.add_argument(
        "--refresh-output",
        type=Path,
        help="Write a refreshed JSONL dataset with live payloads regenerated.",
    )
    args = parser.parse_args()

    db_hash = hashlib.sha256(args.db.read_bytes()).hexdigest()
    records: list[dict[str, Any]] = []
    refreshed_cases: list[dict[str, Any]] = []
    failures: list[str] = []
    for line in args.cases.read_text().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        payload = case["payload"]
        if case.get("source") == "live_query" and args.refresh_output:
            payload = run_query(case, args.db)
            case = {**case, "payload": payload}
        refreshed_cases.append(case)
        record = {
            "case_id": case["case_id"],
            "source": case.get("source"),
            "query": case["query"],
            "query_hash": sha256_text(canonical(case["query"])),
            "payload_hash": sha256_text(canonical(payload)),
            "database_sha256": db_hash,
            "transforms": case.get("transforms", []),
        }
        if args.verify and case.get("source") == "live_query":
            current = run_query(case, args.db)
            if canonical(current) != canonical(payload):
                failures.append(case["case_id"])
                record["current_payload_hash"] = sha256_text(canonical(current))
        records.append(record)

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text("".join(canonical(record) + "\n" for record in records))
    if args.refresh_output:
        args.refresh_output.parent.mkdir(parents=True, exist_ok=True)
        args.refresh_output.write_text(
            "".join(canonical(case) + "\n" for case in refreshed_cases)
        )
    if failures:
        print(f"Payload drift detected: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"Verified {len(records)} cases; manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
