"""Small JSON interface for explicit ia_repomap preparation and evidence queries."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from .config import PrContextRequest, PrepareRepoMapRequest, PrImpactRequest
from .impact import build_symbol_impact
from .pr_analysis import PRAnalysisRequestV1, load_bedrock_settings, run_pr_analysis
from .pr_context import build_pr_context
from .readiness import prepare_repomap
from .review import (
    ReviewSetupError,
    acquire_review_checkout,
    marker_env,
    resolve_pr,
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

    review = commands.add_parser("review", help="run an exact GitHub PR analysis")
    review.add_argument("pr_url", help="canonical GitHub pull-request URL")
    review.add_argument("--repo", dest="repo_root", required=True, help="local repository checkout")
    review.add_argument("--workspace", help="external retained state directory")
    review.add_argument(
        "--inspect",
        action="store_true",
        help="allow the coordinator to inspect source through its bounded tools",
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
            (item for item in raw_args if item in {"prepare", "pr-context", "symbol-impact", "review"}),
            None,
        )
        return _emit_failure(
            out,
            command=command,
            diagnostic=f"invalid command arguments: {exc}",
            remediation=["Run `python -m ia_repomap_builder --help` for valid arguments."],
        )

    command = args.command
    if command == "review":
        return _run_review(args, out)
    if command not in {"prepare", "pr-context", "symbol-impact"}:
        return _emit_failure(
            out,
            command=None,
            diagnostic="a command is required: review, prepare, pr-context, or symbol-impact",
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
            result = prepare_repomap(PrepareRepoMapRequest(repo_root, artifact_root))
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


_GH_UNAVAILABLE_MARKERS = (
    "authentication",
    "not logged into",
    "bad credentials",
    "rate limit",
    "service unavailable",
    "temporarily unavailable",
    "connection refused",
    "connection reset",
    "timed out",
    "timeout",
    "http 502",
    "http 503",
    "http 504",
    "no such file or directory",
    "command not found",
)


def _is_gh_unavailable(error: ReviewSetupError) -> bool:
    """Classify only gh PR lookup auth/service failures as unavailable."""

    detail = str(error).lower()
    return "gh pr view" in detail and any(marker in detail for marker in _GH_UNAVAILABLE_MARKERS)


def _review_envelope(
    *,
    request: dict[str, Any],
    status: str,
    assessment: str,
    base_sha: str | None,
    head_sha: str | None,
    report_directory: Path | None,
    remediation: list[str],
    report: Any | None = None,
) -> dict[str, Any]:
    directory = str(report_directory) if report_directory is not None else None
    report_status = getattr(report, "status", None) if report is not None else None
    report_assessment = getattr(report, "assessment", None) if report is not None else None
    return {
        "schema": COMMAND_SCHEMA,
        "command": "review",
        "request": request,
        "status": status,
        "assessment": assessment,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "report_directory": directory,
        "report": {
            "status": report_status,
            "assessment": report_assessment,
            "files": _report_files(report_directory) if report_directory is not None else [],
        },
        "remediation": remediation,
    }


def _run_review(args: argparse.Namespace, out: TextIO) -> int:
    requested_workspace = workspace_root(args.workspace)
    request = {
        "pr_url": args.pr_url,
        "repo": str(Path(args.repo_root).expanduser().resolve()),
        "workspace": str(requested_workspace),
        "inspect": bool(args.inspect),
    }
    metadata = None
    report_directory: Path | None = None
    try:
        metadata = resolve_pr(args.pr_url)
    except ReviewSetupError as exc:
        unavailable = _is_gh_unavailable(exc)
        diagnostic = (
            f"GitHub CLI unavailable: {exc}. Check `gh auth status`, network access, "
            "and GitHub service availability, then rerun."
            if unavailable
            else str(exc)
        )
        return _write_review_result(
            out,
            _review_envelope(
                request=request,
                status="unavailable" if unavailable else "error",
                assessment="unavailable" if unavailable else "error",
                base_sha=None,
                head_sha=None,
                report_directory=None,
                remediation=[diagnostic, "Correct the PR URL or GitHub CLI environment, then rerun."],
            ),
        )

    try:
        checkout = acquire_review_checkout(
            metadata,
            repo=Path(args.repo_root),
            workspace=requested_workspace,
        )
        workspace = checkout.workspace_root
        artifact_root = workspace / "artifacts"
        report_directory = (
            workspace
            / "reports"
            / _safe_repository_name(metadata.repository)
            / metadata.head_sha
            / metadata.base_sha
            / uuid.uuid4().hex
        )
        request.update(
            {
                "base_sha": metadata.base_sha,
                "head_sha": metadata.head_sha,
                "worktree": str(checkout.worktree),
                "artifact_root": str(artifact_root),
                "report_directory": str(report_directory),
            }
        )
        with marker_env():
            prepared = prepare_repomap(
                PrepareRepoMapRequest(checkout.worktree, artifact_root)
            )
            if prepared.status != "ok":
                status = prepared.status if prepared.status in {"unavailable", "error"} else "error"
                assessment = status
                return _write_review_result(
                    out,
                    _review_envelope(
                        request=request,
                        status=status,
                        assessment=assessment,
                        base_sha=metadata.base_sha,
                        head_sha=metadata.head_sha,
                        report_directory=report_directory,
                        remediation=_remediation("review", prepared),
                    ),
                )
            try:
                settings = load_bedrock_settings()
            except (OSError, TypeError, ValueError) as exc:
                return _write_review_result(
                    out,
                    _review_envelope(
                        request=request,
                        status="unavailable",
                        assessment="unavailable",
                        base_sha=metadata.base_sha,
                        head_sha=metadata.head_sha,
                        report_directory=report_directory,
                        remediation=[
                            f"Bedrock settings unavailable: {exc}. Set AWS_REGION or "
                            "AWS_DEFAULT_REGION and BEDROCK_MODEL_ID in the process "
                            "environment or .env.local, then rerun."
                        ],
                    ),
                )
            report = run_pr_analysis(
                PRAnalysisRequestV1(
                    schema_="ia-repomap.pr-analysis-request/v1",
                    repo_root=checkout.worktree,
                    base_ref=metadata.base_sha,
                    artifact_root=artifact_root,
                    output_dir=report_directory,
                ),
                settings=settings,
                allow_source_inspection=bool(args.inspect),
            )
        status = report.status if report.status in {"ok", "unavailable", "error"} else "error"
        return _write_review_result(
            out,
            _review_envelope(
                request=request,
                status=status,
                assessment=report.assessment,
                base_sha=metadata.base_sha,
                head_sha=metadata.head_sha,
                report_directory=report_directory,
                remediation=_remediation("review", report),
                report=report,
            ),
        )
    except ReviewSetupError as exc:
        return _write_review_result(
            out,
            _review_envelope(
                request=request,
                status="error",
                assessment="error",
                base_sha=metadata.base_sha if metadata else None,
                head_sha=metadata.head_sha if metadata else None,
                report_directory=report_directory,
                remediation=[str(exc), "Correct the local checkout or PR input, then rerun."],
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive public command boundary
        return _write_review_result(
            out,
            _review_envelope(
                request=request,
                status="error",
                assessment="error",
                base_sha=metadata.base_sha if metadata else None,
                head_sha=metadata.head_sha if metadata else None,
                report_directory=report_directory,
                remediation=[f"{type(exc).__name__}: {exc}", "Correct the setup or configuration, then rerun."],
            ),
        )


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
    else:
        result["changed_files"] = []
    if command == "review":
        payload = {
            "schema": COMMAND_SCHEMA,
            "command": "review",
            "request": request or {},
            "status": "error",
            "assessment": "error",
            "base_sha": None,
            "head_sha": None,
            "report_directory": None,
            "report": {"status": None, "assessment": None, "files": []},
            "result": result,
            "remediation": remediation,
        }
        _write_payload(out, payload)
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
    if any(
        marker in joined
        for marker in ("symbol not found", "symbol is ambiguous", "ambiguous symbol")
    ):
        actions.append("Use the exact symbol path and name returned by `pr-context`, then rerun.")
    if "base_ref cannot be resolved" in joined:
        actions.append("Provide a resolvable local base ref (for example origin/main), then rerun.")
    if not actions and result.status != "ok":
        actions.append(f"Inspect the {command} diagnostics, correct the prerequisite, and rerun.")
    return actions


if __name__ == "__main__":  # pragma: no cover - exercised via module execution
    raise SystemExit(main())
