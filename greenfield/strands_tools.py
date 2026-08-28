"""Bounded, read-only repository tools exposed to the Greenfield Strands agent."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from greenfield.artifact_io import artifact_sha256
from greenfield.run_context import validate_run_context

SHA = re.compile(r"^[0-9a-f]{40}$")


class GreenfieldToolError(ValueError):
    """Raised when a tool request exceeds its immutable read boundary."""


class GreenfieldToolbox:
    """Revision-bound tool implementations with an inspectable call ledger."""

    def __init__(self, run_context: Mapping[str, Any]) -> None:
        errors = validate_run_context(run_context)
        if errors:
            raise GreenfieldToolError("invalid run context: " + "; ".join(errors))
        self.context = run_context
        self.calls: list[dict[str, Any]] = []
        self._limits = run_context["tool_policy"]["limits"]
        self._repositories = self._repository_map()
        self._handbooks = {
            str(row["repository"]): {
                "path": Path(str(row["path"])),
                "sha256": str(row["sha256"]),
            }
            for row in run_context.get("repository_handbooks", [])
            if isinstance(row, Mapping)
        }
        self._artifacts: dict[str, Path] = {}
        for row in run_context.get("evidence_artifacts", []):
            if not isinstance(row, Mapping):
                continue
            path = Path(str(row["path"]))
            if path.name in self._artifacts:
                raise GreenfieldToolError(
                    f"captured evidence basenames must be unique: {path.name}"
                )
            self._artifacts[path.name] = {
                "path": path,
                "sha256": str(row["sha256"]),
            }

    def _repository_map(self) -> dict[str, dict[str, Any]]:
        source = dict(self.context["source"])
        result = {
            str(source["repository"]): {
                "repository": source["repository"],
                "repo_key": source.get("repo_key"),
                "local_root": source.get("local_root"),
                "inspected_revision": source["head_revision"],
                "priority": "source",
                "test_roots": [],
                "test_formats": [],
            }
        }
        for row in self.context["candidate_repositories"]:
            result[str(row["repository"])] = dict(row)
        return result

    def _record(
        self, name: str, arguments: Mapping[str, Any], result: Mapping[str, Any]
    ) -> dict[str, Any]:
        if len(self.calls) >= int(self._limits["max_tool_calls"]):
            raise GreenfieldToolError("maximum tool-call budget exceeded")
        call_id = artifact_sha256(
            {"sequence": len(self.calls) + 1, "tool": name, "arguments": arguments}
        )
        snapshot = deepcopy(dict(result))
        result_sha256 = artifact_sha256(snapshot)
        row = {
            "tool_call_id": call_id,
            "tool": name,
            "arguments": dict(arguments),
            "result_sha256": result_sha256,
            "result": snapshot,
            "status": snapshot.get("status", "available"),
        }
        self.calls.append(row)
        return {
            **snapshot,
            "tool_call_id": call_id,
            "result_sha256": result_sha256,
        }

    def _captured_bytes(self, path: Path, expected_sha256: str) -> bytes:
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise GreenfieldToolError(
                f"captured evidence is unreadable: {path}"
            ) from exc
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise GreenfieldToolError(
                f"captured evidence changed after Capture: {path}"
            )
        if len(content) > int(self._limits["max_file_bytes"]):
            raise GreenfieldToolError(
                f"captured evidence exceeds max_file_bytes: {path}"
            )
        return content

    @staticmethod
    def _safe_path(value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value.strip():
            raise GreenfieldToolError("path must be a safe repository-relative path")
        return value

    def _repository(
        self, repository: str, revision: str | None = None
    ) -> tuple[dict[str, Any], Path, str]:
        row = self._repositories.get(repository)
        if row is None:
            raise GreenfieldToolError(
                f"repository is outside captured scope: {repository}"
            )
        root = Path(str(row.get("local_root") or "")).resolve()
        if not root.is_dir():
            raise GreenfieldToolError(
                f"repository checkout is unavailable: {repository}"
            )
        expected = str(row.get("inspected_revision") or "")
        requested = revision or expected
        if not SHA.fullmatch(requested):
            raise GreenfieldToolError(f"captured revision is unavailable: {repository}")
        if requested != expected:
            raise GreenfieldToolError(
                f"revision differs from captured repository snapshot: {repository}"
            )
        return row, root, requested

    @staticmethod
    def _git(
        root: Path, *args: str, timeout: int = 30
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            timeout=timeout,
        )

    def list_candidate_repositories(self) -> dict[str, Any]:
        """List explicit-contract candidates first, then discovery screening scope."""

        result = {
            "status": "available",
            "repositories": self.context["candidate_repositories"],
        }
        return self._record("list_candidate_repositories", {}, result)

    def repository_metadata(self, repository: str) -> dict[str, Any]:
        """Return captured manifest metadata for one in-scope repository."""

        row = self._repositories.get(repository)
        result = (
            {"status": "available", "repository": dict(row)}
            if row is not None
            else {"status": "unavailable", "reason": "repository_outside_scope"}
        )
        return self._record("repository_metadata", {"repository": repository}, result)

    def read_source(
        self,
        repository: str,
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> dict[str, Any]:
        """Read a bounded source excerpt from the captured repository revision."""

        row, root, revision = self._repository(repository)
        safe_path = self._safe_path(path)
        if start_line < 1 or end_line < start_line or end_line - start_line > 500:
            raise GreenfieldToolError(
                "source line range is invalid or exceeds 500 lines"
            )
        command = self._git(root, "show", f"{revision}:{safe_path}")
        if command.returncode:
            result: dict[str, Any] = {
                "status": "unavailable",
                "repository": repository,
                "source_revision": revision,
                "path": safe_path,
            }
        elif len(command.stdout) > int(self._limits["max_file_bytes"]):
            result = {
                "status": "unavailable",
                "reason": "file_size_budget_exceeded",
                "repository": repository,
                "source_revision": revision,
                "path": safe_path,
            }
        else:
            lines = command.stdout.decode("utf-8", errors="replace").splitlines()
            excerpt = "\n".join(
                f"{number}: {lines[number - 1]}"
                for number in range(start_line, min(end_line, len(lines)) + 1)
            )
            result = {
                "status": "available",
                "repository": repository,
                "repo_key": row.get("repo_key"),
                "source_revision": revision,
                "path": safe_path,
                "start_line": start_line,
                "end_line": min(end_line, len(lines)),
                "content_sha256": hashlib.sha256(command.stdout).hexdigest(),
                "excerpt": excerpt,
            }
        return self._record(
            "read_source",
            {
                "repository": repository,
                "path": safe_path,
                "start_line": start_line,
                "end_line": end_line,
            },
            result,
        )

    def search_source(
        self,
        repository: str,
        query: str,
        path_prefix: str = "",
        max_results: int = 20,
    ) -> dict[str, Any]:
        """Search literal text at the captured revision without checking out files."""

        _, root, revision = self._repository(repository)
        if not query or len(query) > 500:
            raise GreenfieldToolError("query must contain 1-500 characters")
        limit = min(max_results, int(self._limits["max_search_results"]))
        if limit < 1:
            raise GreenfieldToolError("max_results must be positive")
        args = ["grep", "-n", "-F", query, revision]
        if path_prefix:
            args.extend(["--", self._safe_path(path_prefix)])
        command = self._git(root, *args)
        matches = command.stdout.decode("utf-8", errors="replace").splitlines()[:limit]
        result = {
            "status": "available" if command.returncode in {0, 1} else "unavailable",
            "repository": repository,
            "source_revision": revision,
            "matches": matches,
            "truncated": len(matches) == limit,
        }
        return self._record(
            "search_source",
            {
                "repository": repository,
                "query": query,
                "path_prefix": path_prefix,
                "max_results": max_results,
            },
            result,
        )

    def read_handbook(self, repository: str, section: str = "index") -> dict[str, Any]:
        """Read an L1/L2/L3 repository handbook section from captured storage."""

        path = self._handbooks.get(repository)
        if path is None:
            result: dict[str, Any] = {
                "status": "unavailable",
                "reason": "repository_handbook_not_supplied",
            }
        else:
            captured = self._captured_bytes(
                path, str(self._handbooks[repository]["sha256"])
            )
            try:
                value = json.loads(captured.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GreenfieldToolError(
                    f"repository handbook is not valid JSON: {path}"
                ) from exc
            sections = value.get("sections", {})
            selected = sections.get(section) if isinstance(sections, Mapping) else None
            result = {
                "status": "available" if selected is not None else "unavailable",
                "repository": repository,
                "source_revision": self._repositories[repository]["inspected_revision"],
                "handbook_sha256": artifact_sha256(value),
                "section": section,
                "content": selected,
            }
        return self._record(
            "read_handbook", {"repository": repository, "section": section}, result
        )

    def read_evidence_artifact(self, name: str) -> dict[str, Any]:
        """Read one explicitly captured JSON evidence artifact by basename."""

        path = self._artifacts.get(name)
        if path is None:
            result = {"status": "unavailable", "reason": "artifact_not_captured"}
        else:
            captured = self._captured_bytes(path["path"], path["sha256"])
            text = captured.decode("utf-8")
            if path["path"].suffix.lower() in {".yaml", ".yml"}:
                content = yaml.safe_load(text)
            else:
                try:
                    content = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise GreenfieldToolError(
                        f"evidence artifact is not valid JSON: {path['path']}"
                    ) from exc
            result = {
                "status": "available",
                "name": name,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "content": content,
            }
        return self._record("read_evidence_artifact", {"name": name}, result)

    def codegraph_explore(self, repository: str, question: str) -> dict[str, Any]:
        """Explore CodeGraph only when its index matches the captured checkout HEAD."""

        _, root, revision = self._repository(repository)
        if not (root / ".codegraph").exists():
            result: dict[str, Any] = {
                "status": "unavailable",
                "reason": "codegraph_index_unavailable",
            }
        else:
            head = self._git(root, "rev-parse", "HEAD").stdout.decode().strip()
            if head != revision:
                result = {
                    "status": "unavailable",
                    "reason": "codegraph_revision_mismatch",
                }
            elif not question.strip() or len(question) > 2_000:
                raise GreenfieldToolError(
                    "CodeGraph question must contain 1-2000 characters"
                )
            else:
                command = subprocess.run(
                    ["codegraph", "explore", question],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                result = {
                    "status": "available" if command.returncode == 0 else "unavailable",
                    "repository": repository,
                    "source_revision": revision,
                    "output": command.stdout[: int(self._limits["max_file_bytes"])],
                }
        return self._record(
            "codegraph_explore",
            {"repository": repository, "question": question},
            result,
        )

    def as_strands_tools(
        self, decorator: Callable[[Callable[..., Any]], Any]
    ) -> list[Any]:
        """Decorate the bounded methods using the installed Strands `tool` contract."""

        return [
            decorator(self.list_candidate_repositories),
            decorator(self.repository_metadata),
            decorator(self.read_handbook),
            decorator(self.read_source),
            decorator(self.search_source),
            decorator(self.read_evidence_artifact),
            decorator(self.codegraph_explore),
        ]

    def ledger(self) -> list[dict[str, Any]]:
        return json.loads(json.dumps(self.calls))


__all__ = ["GreenfieldToolError", "GreenfieldToolbox"]
