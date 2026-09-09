"""Small JSON interface for explicit ia_repomap preparation and PR context."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from .config import PrContextRequest, PrepareRepoMapRequest
from .pr_context import build_pr_context
from .readiness import prepare_repomap

COMMAND_SCHEMA = "ia-repomap.command-result/v1"
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_UNAVAILABLE = 3


class _ArgumentParser(argparse.ArgumentParser):
    """Raise a normal exception so argument failures can also be JSON."""

    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m ia_repomap_builder",
        description="Prepare and consume revision-bound ia_repomap context.",
    )
    commands = parser.add_subparsers(dest="command", parser_class=_ArgumentParser)

    prepare = commands.add_parser("prepare", help="prepare an external Ripwire index")
    _add_common_arguments(prepare)

    context = commands.add_parser("pr-context", help="run revision-bound PR-context analysis")
    _add_common_arguments(context)
    context.add_argument("--base", dest="base_ref", help="Git base reference")
    context.add_argument("--token-budget", type=int, default=None)
    context.add_argument("--limit", type=int, default=20)
    context.add_argument("--offset", type=int, default=0)
    context.add_argument("--history-commits", type=int, default=500)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", dest="repo_root", help="target repository checkout")
    parser.add_argument("--artifact-root", help="external ia_repomap artifact directory")
    parser.add_argument("--output", help="optional external JSON output path")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the local JSON module interface and return a process exit code.

    ``pr-context`` treats the clean checkout ``HEAD`` as the PR head and
    accepts an explicit local base ref; it does not accept or resolve a PR
    number through GitHub.
    """

    out = stdout if stdout is not None else sys.stdout
    _ = stderr if stderr is not None else sys.stderr
    parser = _parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse uses SystemExit for --help. It has already rendered the
        # help text, so preserve that conventional behavior.
        return int(exc.code) if isinstance(exc.code, int) else EXIT_ERROR
    except ValueError as exc:
        return _emit_failure(
            out,
            command=None,
            diagnostic=f"invalid command arguments: {exc}",
            remediation=["Run `python -m ia_repomap_builder --help` for valid arguments."],
        )

    command = args.command
    if command not in {"prepare", "pr-context"}:
        return _emit_failure(
            out,
            command=None,
            diagnostic="a command is required: prepare or pr-context",
            remediation=["Run `python -m ia_repomap_builder --help` for valid commands."],
        )

    missing = [name for name in ("repo_root", "artifact_root") if not getattr(args, name, None)]
    if command == "pr-context" and not getattr(args, "base_ref", None):
        missing.append("base")
    if missing:
        return _emit_failure(
            out,
            command=command,
            diagnostic=f"missing required argument(s): {', '.join('--' + name.replace('_', '-') for name in missing)}",
            remediation=[f"Provide {', '.join('--' + name.replace('_', '-') for name in missing)} and rerun."],
        )

    repo_root = Path(args.repo_root).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    output = Path(args.output).resolve() if args.output else None
    output_error = _validate_output_path(repo_root, output)
    if output_error:
        return _emit_failure(
            out,
            command=command,
            diagnostic=output_error,
            remediation=["Choose a new output path outside the target repository."],
        )

    request_payload: dict[str, Any] = {
        "repo_root": str(repo_root),
        "artifact_root": str(artifact_root),
    }
    try:
        if command == "prepare":
            result = prepare_repomap(PrepareRepoMapRequest(repo_root, artifact_root))
        else:
            request_payload.update(
                {
                    "base_ref": args.base_ref,
                    "token_budget": args.token_budget,
                    "limit": args.limit,
                    "offset": args.offset,
                    "history_commits": args.history_commits,
                }
            )
            result = build_pr_context(
                PrContextRequest(
                    repo_root=repo_root,
                    artifact_root=artifact_root,
                    base_ref=args.base_ref,
                    token_budget=args.token_budget,
                    limit=args.limit,
                    offset=args.offset,
                    history_commits=args.history_commits,
                )
            )
    except Exception as exc:  # pragma: no cover - defensive command boundary
        return _emit_failure(
            out,
            command=command,
            request=request_payload,
            diagnostic=f"{command} execution failed: {type(exc).__name__}: {exc}",
            remediation=["Inspect the diagnostic, correct the environment or input, and rerun."],
        )

    payload = _envelope(command, request_payload, result)
    remediation = _remediation(command, result)
    payload["remediation"] = remediation
    if output is not None:
        try:
            _write_json_once(output, payload)
        except OSError as exc:
            return _emit_failure(
                out,
                command=command,
                request=request_payload,
                diagnostic=f"cannot write JSON output: {exc}",
                remediation=["Choose a writable, new path outside the target repository."],
            )
    _write_payload(out, payload)
    return _exit_code(result.status)


def _envelope(command: str, request: dict[str, Any], result: Any) -> dict[str, Any]:
    result_dict = result.as_dict()
    return {
        "schema": COMMAND_SCHEMA,
        "command": command,
        "request": request,
        "status": result_dict.get("status", "error"),
        "result": result_dict,
        "remediation": [],
    }


def _emit_failure(
    out: TextIO,
    *,
    command: str | None,
    diagnostic: str,
    remediation: list[str],
    request: dict[str, Any] | None = None,
) -> int:
    result = {
        "status": "error",
        "diagnostics": [diagnostic],
        "changed_files": [],
        "raw_xml": "",
        "gaps": [],
        "metrics": {},
        "identity": {},
    }
    payload = {
        "schema": COMMAND_SCHEMA,
        "command": command,
        "request": request or {},
        "status": "error",
        "result": result,
        "remediation": remediation,
    }
    _write_payload(out, payload)
    return EXIT_ERROR


def _validate_output_path(repo_root: Path, output: Path | None) -> str | None:
    if output is None:
        return None
    try:
        output.relative_to(repo_root)
    except ValueError:
        pass
    else:
        return "output path must be outside the target repository"
    if output.exists():
        return f"output path already exists: {output}"
    return None


def _write_json_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise OSError(f"output path already exists: {path}")
    encoded = _encode_payload(payload)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass


def _encode_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_payload(out: TextIO, payload: dict[str, Any]) -> None:
    out.write(_encode_payload(payload))
    out.flush()


def _exit_code(status: str) -> int:
    if status == "ok":
        return EXIT_OK
    if status == "unavailable":
        return EXIT_UNAVAILABLE
    return EXIT_ERROR


def _remediation(command: str, result: Any) -> list[str]:
    diagnostics = [str(item).lower() for item in result.diagnostics]
    actions: list[str] = []
    joined = " ".join(diagnostics)
    if "repository root is not a directory" in joined:
        actions.append("Point --repo to an existing Git checkout, then rerun.")
    if "not a readable git checkout" in joined:
        actions.append("Point --repo to a readable Git checkout, then rerun.")
    if "artifact_root must remain outside" in joined:
        actions.append("Choose an --artifact-root outside the target repository, then rerun.")
    if "marker is missing" in joined:
        actions.append("Add the approved root .ia-repomap.toml declaration, then rerun.")
    if "invalid repository marker" in joined:
        actions.append("Fix the root .ia-repomap.toml using the v1 template, then rerun.")
    if "must be clean" in joined:
        actions.append("Use a clean checkout (or commit/stash intentional changes), then rerun.")
    if "manifest is missing" in joined or "does not match" in joined:
        actions.append("Run the explicit `prepare` command for this exact clean HEAD, then rerun.")
    if (
        "binary unavailable" in joined
        or "version check failed" in joined
        or "does not support --pr-history-commits" in joined
        or "cannot be consumed by the current ripwire binary" in joined
    ):
        actions.append("Set RIPWIRE_BIN to a compatible patched Ripwire binary, then rerun.")
    if "base_ref cannot be resolved" in joined:
        actions.append("Provide a resolvable local base ref (for example origin/main), then rerun.")
    if not actions and result.status != "ok":
        actions.append(f"Inspect the {command} diagnostics, correct the prerequisite, and rerun.")
    return actions


if __name__ == "__main__":  # pragma: no cover - exercised via module execution
    raise SystemExit(main())
