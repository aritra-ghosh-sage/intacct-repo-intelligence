"""Optional adapters for Aider RepoMap and Ripwire.

Both adapters are intentionally defensive.  Their availability and API
surface are environment facts, so a mismatch is returned as a diagnostic.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import PHP_FAMILY_EXTENSIONS, BuildRequest, BuildResult, ContextItem
from .files import is_intacct_source_path, iter_files


def _unavailable(engine: str, message: str) -> BuildResult:
    return BuildResult(engine=engine, status="unavailable", diagnostics=[message])


class _AiderIO:
    """Minimal non-interactive sink for RepoMap diagnostics."""

    def read_text(self, filename):
        try:
            return Path(filename).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def tool_warning(self, *_args, **_kwargs):
        return None

    def tool_error(self, *_args, **_kwargs):
        return None

    def tool_output(self, *_args, **_kwargs):
        return None


class _AiderModel:
    """Tokenizer-compatible fallback; RepoMap does not need an LLM call."""

    @staticmethod
    def token_count(text):
        return len(text) // 4


def build_aider(request: BuildRequest) -> BuildResult:
    try:
        import aider.repomap as repomap_module
        import grep_ast as grep_ast_module
        import grep_ast.grep_ast as grep_ast_impl
    except ImportError:
        return _unavailable("aider", "Aider is not installed; install a pinned Aider release to run this arm")

    # Aider's language table is filename-based.  Patch only the imported
    # resolver for this process so Intacct aliases are tested without changing
    # the user's checkout or claiming that upstream supports them.
    original = getattr(repomap_module, "filename_to_lang", None)
    grep_ast_original = getattr(grep_ast_module, "filename_to_lang", None)
    grep_ast_impl_original = getattr(grep_ast_impl, "filename_to_lang", None)
    if original is None or grep_ast_original is None or grep_ast_impl_original is None:
        return _unavailable("aider", "Aider RepoMap has no filename_to_lang seam; API is unsupported")

    def intacct_filename_to_lang(filename: str):
        path = Path(filename)
        if path.suffix.lower() in PHP_FAMILY_EXTENSIONS and is_intacct_source_path(request.repo_root, path):
            return original(str(path.with_suffix(".php")))
        return original(filename)

    # RepoMap and grep-ast's TreeContext each hold their own imported
    # resolver reference. Patch both for this process, then restore them in
    # the finally block so unrelated callers retain Aider's defaults.
    repomap_module.filename_to_lang = intacct_filename_to_lang
    grep_ast_module.filename_to_lang = intacct_filename_to_lang
    grep_ast_impl.filename_to_lang = intacct_filename_to_lang
    started = time.perf_counter()
    try:
        cls = repomap_module.RepoMap
        parameters = inspect.signature(cls).parameters
        kwargs = {}
        for name, parameter in parameters.items():
            if name in {"map_tokens", "token_budget"}:
                kwargs[name] = request.token_budget
            elif name in {"root", "repo_root"}:
                kwargs[name] = str(request.repo_root)
            elif name in {"main_model", "model"}:
                kwargs[name] = _AiderModel()
            elif name == "io":
                kwargs[name] = _AiderIO()
        mapper = cls(**kwargs)
        files = [str(path) for path in iter_files(request.repo_root, request.scope)]
        method = mapper.get_repo_map
        method_params = inspect.signature(method).parameters
        call_kwargs = {}
        for name in method_params:
            if name == "chat_files":
                call_kwargs[name] = []
            elif name == "other_files":
                call_kwargs[name] = files
            elif name == "mentioned_fnames":
                call_kwargs[name] = []
            elif name in {"mentioned_ids", "mentioned_idents"}:
                # Aider releases have used both names. Forward the task
                # query so RepoMap ranks symbols relevant to this request.
                call_kwargs[name] = {request.query} if request.query else set()
        context = method(**call_kwargs)
    except Exception as exc:  # pragma: no cover - depends on optional Aider versions
        return _unavailable("aider", f"Aider RepoMap API/engine failure: {type(exc).__name__}: {exc}")
    finally:
        repomap_module.filename_to_lang = original
        grep_ast_module.filename_to_lang = grep_ast_original
        grep_ast_impl.filename_to_lang = grep_ast_impl_original

    return BuildResult(
        engine="aider",
        status="ok",
        context=context or "",
        diagnostics=["Aider output is rendered prompt text; call edges are not separately represented"],
        metrics={
            "files_seen": len(files),
            "files": [str(Path(path).resolve().relative_to(request.repo_root.resolve())) for path in files],
            "estimated_tokens": len((context or "")) // 4,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )


def _ripwire_binary() -> str | None:
    configured = os.environ.get("RIPWIRE_BIN")
    if configured:
        return configured if Path(configured).is_file() else None
    return shutil.which("ripwire")


def _parse_ripwire_xml(root: Path, output: str, path_prefix: str = "") -> list[ContextItem]:
    try:
        xml_root = ET.fromstring(output)
    except ET.ParseError as exc:
        raise ValueError(f"Ripwire XML parse failed: {exc}") from exc

    def normalize_path(path: str) -> str | None:
        if Path(path).is_absolute():
            try:
                return Path(path).resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return None
        if path_prefix and path_prefix != ".":
            prefix = path_prefix.rstrip("/")
            if path != prefix and not path.startswith(f"{prefix}/"):
                return f"{prefix}/{path.lstrip('./')}"
        return path

    def parse_line(node: ET.Element) -> int | None:
        value = node.attrib.get("l", "")
        return int(value) if value.isdigit() else None

    def parse_score(node: ET.Element, attribute: str) -> float | None:
        value = node.attrib.get(attribute)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    items: list[ContextItem] = []
    for parent in xml_root.iter():
        if parent.tag == "sigs":
            for node in parent.findall("./d"):
                raw_path = node.attrib.get("p")
                if not raw_path:
                    continue
                path = normalize_path(raw_path)
                if path is None:
                    continue
                items.append(
                    ContextItem(
                        path=path,
                        symbol=node.attrib.get("n"),
                        line=parse_line(node),
                        score=parse_score(node, "r"),
                        evidence="ripwire-ranked-symbol",
                    )
                )
        elif parent.tag == "f":
            raw_path = parent.attrib.get("p")
            if not raw_path:
                continue
            path = normalize_path(raw_path)
            if path is None:
                continue
            for node in parent.findall("./s"):
                items.append(
                    ContextItem(
                        path=path,
                        symbol=node.attrib.get("n"),
                        line=parse_line(node),
                        score=parse_score(node, "k"),
                        evidence="ripwire-ranked-symbol",
                    )
                )
    return items


def build_ripwire(request: BuildRequest) -> BuildResult:
    source_root = (request.repo_root / "app/source").resolve()
    unsafe_scopes = []
    for raw_scope in request.scope:
        scope_path = (request.repo_root / raw_scope).resolve()
        try:
            scope_path.relative_to(source_root)
        except ValueError:
            unsafe_scopes.append(raw_scope)
    if unsafe_scopes:
        return BuildResult(
            engine="ripwire",
            status="error",
            diagnostics=[
                "Ripwire scopes must stay within app/source because its binary cannot exclude "
                f"third-party .map files: {', '.join(unsafe_scopes)}"
            ],
        )

    binary = _ripwire_binary()
    if binary is None:
        return _unavailable("ripwire", "Ripwire binary unavailable; set RIPWIRE_BIN to a pinned build")

    # Run each scope as its own root.  This makes the requested app/source
    # boundary exact even though Ripwire's general map command is root-based.
    contexts: list[str] = []
    items: list[ContextItem] = []
    started = time.perf_counter()
    try:
        for scope in request.scope:
            scope_path = (request.repo_root / scope).resolve()
            # The XML form is the stable human/agent context surface and is
            # parsed here for ranked symbol rows. JSON remains available to a
            # future machine-only adapter.
            command = [binary, str(scope_path), f"--token-budget={request.token_budget}"]
            if request.query:
                command.append(f"--for={request.query}")
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if completed.returncode != 0:
                return BuildResult(
                    engine="ripwire",
                    status="error",
                    diagnostics=[
                        f"Ripwire exited {completed.returncode}: {completed.stderr.strip()[:500]}"
                    ],
                )
            contexts.append(completed.stdout)
            try:
                items.extend(_parse_ripwire_xml(scope_path, completed.stdout, scope))
            except ValueError as exc:
                return BuildResult(engine="ripwire", status="error", diagnostics=[str(exc)])
    except (OSError, subprocess.SubprocessError) as exc:
        return BuildResult(engine="ripwire", status="error", diagnostics=[f"Ripwire invocation failed: {exc}"])

    return BuildResult(
        engine="ripwire",
        status="ok",
        items=items,
        context="\n".join(contexts),
        diagnostics=[
            "Ripwire extension aliases must be present in the pinned binary; .ripwire_config cannot add them"
        ],
        metrics={
            "scopes": len(request.scope),
            "estimated_tokens": sum(len(context) for context in contexts) // 4,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
