from __future__ import annotations

import json
from typing import Any

import click

CONTRACT_VERSION = 1


def emit_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=True))


def success_response(
    *,
    command: str,
    args: dict[str, Any],
    data: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "query": {
            "command": command,
            "args": args,
        },
        "status": "ok",
        "data": data,
        "summary": summary,
        "error": None,
    }


def error_response(
    *,
    command: str,
    args: dict[str, Any],
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "query": {
            "command": command,
            "args": args,
        },
        "status": "error",
        "data": {},
        "summary": {},
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }
