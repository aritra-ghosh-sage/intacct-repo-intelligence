"""Small JSON interface for explicit ia_repomap preparation and evidence queries."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from .config import PrContextRequest, PrImpactRequest
from .impact import build_symbol_impact
from .pr_context import build_pr_context
from .readiness import prepare_repomap
from .test_inventory import build_test_inventory, persist_test_inventory
from .review import (
    ReviewRequest,
    ReviewRunResult,
    ReviewSetupResult,
    prepare_repomap_for_cli as prepare_repomap,
    run_review,
    setup_review,
    workspace_root,
)

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

    impact = commands.add_parser(
        "symbol-impact", help="expand one prepared symbol into lower-bound impact evidence"
    )
    _add_common_arguments(impact)
    impact.add_argument("--symbol-path", dest="symbol_path", help="repository-relative symbol file path")
    impact.add_argument("--symbol-name", dest="symbol_name", help="indexed symbol name")
    impact.add_argument("--limit", type=int, default=20)
    impact.add_argument("--offset", type=int, default=0)

    inventory = commands.add_parser(
        "test-inventory", help="discover and persist deterministic test-suite inventory"
    )
    _add_common_arguments(inventory)

    setup = commands.add_parser("setup", help="prepare an exact GitHub PR review checkout")
    setup.add_argument("pr_url", help="canonical GitHub pull-request URL")
    setup.add_argument("--repo", dest="repo_root", required=True, help="local repository checkout")
    setup.add_argument("--workspace", help="external retained state directory")

    review = commands.add_parser("review", help="run an exact GitHub PR analysis")
    review.add_argument("pr_url", help="canonical GitHub pull-request URL")
    review.add_argument("--repo", dest="repo_root", required=True, help="local repository checkout")
    review.add_argument("--workspace", help="external retained state directory")
    review.add_argument(
        "--inspect",
        action="store_true",
        help="allow the coordinator to inspect source through its bounded tools",
    )
    review.add_argument(
        "--test-inventory",
        dest="test_inventory_path",
        help="path to a persisted test-inventory.json artifact for coverage cross-reference",
    )
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
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = parser.parse_args(raw_args if argv is not None else None)
    except SystemExit as exc:
        # argparse uses SystemExit for --help. It has already rendered the
        # help text, so preserve that conventional behavior.
        return int(exc.code) if isinstance(exc.code, int) else EXIT_ERROR
    except ValueError as exc:
        command = next(
            (item for item in raw_args if item in {"prepare", "pr-context", "symbol-impact", "test-inventory", "setup", "review"}),
            None,
        )
        return _emit_failure(
            out,
            command=command,
            diagnostic=f"invalid command arguments: {exc}",
            remediation=["Run `python -m ia_repomap_builder --help` for valid arguments."],
        )

    command = args.command
    if command == "setup":
        return _run_setup(args, out)
    if command == "review":
        return _run_review(args, out)
    if command not in {"prepare", "pr-context", "symbol-impact", "test-inventory"}:
        return _emit_failure(
            out,
            command=None,
            diagnostic="a command is required: setup, review, prepare, pr-context, symbol-impact, or test-inventory",
            remediation=["Run `python -m ia_repomap_builder --help` for valid commands."],
        )

    missing = [name for name in ("repo_root", "artifact_root") if not getattr(args, name, None)]
    if command == "pr-context" and not getattr(args, "base_ref", None):
        missing.append("base")
    if command == "symbol-impact":
        for name in ("symbol_path", "symbol_name"):
            if not getattr(args, name, None):
                missing.append(name)
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
    request_payload: dict[str, Any] = {
        "repo_root": str(repo_root),
        "artifact_root": str(artifact_root),
    }
    if command == "pr-context":
        request_payload.update(
            {
                "base_ref": args.base_ref,
                "token_budget": args.token_budget,
                "limit": args.limit,
                "offset": args.offset,
                "history_commits": args.history_commits,
            }
        )
    elif command == "symbol-impact":
        request_payload.update(
            {
                "symbol_path": args.symbol_path,
                "symbol_name": args.symbol_name,
                "limit": args.limit,
                "offset": args.offset,
            }
        )
    output_error = _validate_output_path(repo_root, output)
    if output_error:
        return _emit_failure(
            out,
            command=command,
            request=request_payload,
            diagnostic=output_error,
            remediation=["Choose a new output path outside the target repository."],
        )

    try:
        if command == "prepare":
            result = prepare_repomap(repo_root, artifact_root)
        elif command == "test-inventory":
            inventory = build_test_inventory(repo_root)
            if inventory.status != "ok":
                result = inventory
            else:
                persisted = persist_test_inventory(inventory, artifact_root)
                result = {
                    **inventory.as_dict(),
                    "artifact": {
                        "inventory": str(persisted.inventory_path),
                        "manifest": str(persisted.manifest_path),
                    },
                }
        elif command == "pr-context":
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
        else:
            result = build_symbol_impact(
                PrImpactRequest(
                    repo_root=repo_root,
                    artifact_root=artifact_root,
                    symbol_path=args.symbol_path,
                    symbol_name=args.symbol_name,
                    limit=args.limit,
                    offset=args.offset,
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

    if command == "test-inventory" and isinstance(result, dict):
        payload = {
            "schema": COMMAND_SCHEMA,
            "command": command,
            "request": request_payload,
            "status": result.get("status", "error"),
            "result": result,
            "remediation": [],
        }
    else:
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
    result_status = result.get("status", "error") if isinstance(result, dict) else result.status
    return _exit_code(result_status)


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


def _safe_repository_name(repository: str) -> str:
    value = "".join(character if character.isalnum() else "-" for character in repository)
    return value.strip("-") or "repository"


def _report_files(report_directory: Path) -> list[str]:
    if not report_directory.is_dir():
        return []
    return sorted(
        str(path.relative_to(report_directory))
        for path in report_directory.rglob("*")
        if path.is_file()
    )


def _review_request(args: argparse.Namespace) -> tuple[ReviewRequest, dict[str, Any]]:
    requested_workspace = workspace_root(args.workspace)
    test_inventory_path = getattr(args, "test_inventory_path", None)
    request = ReviewRequest(
        pr_url=args.pr_url,
        repo_root=Path(args.repo_root).expanduser().resolve(),
        workspace=requested_workspace,
        inspect=bool(getattr(args, "inspect", False)),
        test_inventory_path=(
            Path(test_inventory_path).expanduser().resolve()
            if test_inventory_path
            else None
        ),
    )
    payload = {
        "pr_url": request.pr_url,
        "repo": str(request.repo_root),
        "workspace": str(requested_workspace),
    }
    if hasattr(args, "inspect"):
        payload["inspect"] = request.inspect
    if request.test_inventory_path is not None:
        payload["test_inventory_path"] = str(request.test_inventory_path)
    return request, payload


def _setup_envelope(request: dict[str, Any], result: ReviewSetupResult) -> dict[str, Any]:
    return _public_envelope("setup", request, result.as_dict())


def _review_envelope(request: dict[str, Any], result: ReviewRunResult) -> dict[str, Any]:
    return _public_envelope("review", request, result.as_dict())


def _public_envelope(
    command: str,
    request: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    values = dict(result)
    values.setdefault("diagnostics", [])
    payload = {
        "schema": COMMAND_SCHEMA,
        "command": command,
        "request": request,
        "status": values.get("status", "error"),
        "result": values,
        "remediation": list(values.get("remediation", [])),
    }
    # Preserve the pre-envelope fields for existing callers while making the
    # nested result the canonical command outcome.
    payload.update(values)
    payload["result"] = values
    return payload


def _run_setup(args: argparse.Namespace, out: TextIO) -> int:
    request, request_payload = _review_request(args)
    try:
        result = setup_review(request)
    except Exception as exc:  # pragma: no cover - defensive public command boundary
        result = ReviewSetupResult(
            status="error",
            remediation=(f"{type(exc).__name__}: {exc}", "Correct the setup prerequisite, then rerun."),
        )
    _write_payload(out, _setup_envelope(request_payload, result))
    return _exit_code(result.status)


def _run_review(args: argparse.Namespace, out: TextIO) -> int:
    request, request_payload = _review_request(args)
    try:
        result = run_review(request)
    except Exception as exc:  # pragma: no cover - defensive public command boundary
        result = ReviewRunResult(
            status="error",
            assessment="error",
            remediation=(f"{type(exc).__name__}: {exc}", "Correct the setup or execution prerequisite, then rerun."),
        )
    _write_payload(out, _review_envelope(request_payload, result))
    return _exit_code(result.status)


def _write_review_result(out: TextIO, payload: dict[str, Any]) -> int:
    _write_payload(out, payload)
    return _exit_code(payload["status"])


def _emit_failure(
    out: TextIO,
    *,
    command: str | None,
    diagnostic: str,
    remediation: list[str],
    request: dict[str, Any] | None = None,
) -> int:
    result = _failure_result(command, diagnostic, remediation)
    if command in {"setup", "review"}:
        _write_payload(out, _public_envelope(command, request or {}, result))
        return EXIT_ERROR
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


def _failure_result(command: str | None, diagnostic: str, remediation: list[str]) -> dict[str, Any]:
    if command == "setup":
        return {
            "status": "error",
            "base_sha": None,
            "head_sha": None,
            "artifact_root": None,
            "report_directory": None,
            "worktree": None,
            "identity": {},
            "readiness": None,
            "remediation": list(remediation),
            "diagnostics": [diagnostic],
        }
    if command == "review":
        return {
            "status": "error",
            "assessment": "error",
            "base_sha": None,
            "head_sha": None,
            "report_directory": None,
            "report": {"status": None, "assessment": None, "files": []},
            "remediation": list(remediation),
            "diagnostics": [diagnostic],
        }
    result = {
        "status": "error",
        "diagnostics": [diagnostic],
        "raw_xml": "",
        "gaps": [],
        "metrics": {},
        "identity": {},
    }
    if command == "symbol-impact":
        result["candidates"] = []
    elif command == "test-inventory":
        result["suites"] = []
        result["gaps"] = []
        result["metrics"] = {}
        result["repository"] = {}
    else:
        result["changed_files"] = []
    return result


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
    if status in {"ok", "partial"}:
        return EXIT_OK
    if status == "unavailable":
        return EXIT_UNAVAILABLE
    return EXIT_ERROR


def _remediation(command: str, result: Any) -> list[str]:
    raw_diagnostics = result.get("diagnostics", []) if isinstance(result, dict) else result.diagnostics
    diagnostics = [str(item).lower() for item in raw_diagnostics]
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
    if any(
        marker in joined
        for marker in ("symbol not found", "symbol is ambiguous", "ambiguous symbol")
    ):
        actions.append("Use the exact symbol path and name returned by `pr-context`, then rerun.")
    if "base_ref cannot be resolved" in joined:
        actions.append("Provide a resolvable local base ref (for example origin/main), then rerun.")
    status = result.get("status", "error") if isinstance(result, dict) else result.status
    if not actions and status != "ok":
        actions.append(f"Inspect the {command} diagnostics, correct the prerequisite, and rerun.")
    return actions


if __name__ == "__main__":  # pragma: no cover - exercised via module execution
    raise SystemExit(main())
