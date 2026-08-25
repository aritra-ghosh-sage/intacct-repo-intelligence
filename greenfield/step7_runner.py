"""Execution boundary for Greenfield Step 7 validation commands."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from greenfield.step7_contract import artifact_sha256


@dataclass(frozen=True)
class RunnerResult:
    """Bounded command result returned by a Step 7 runner."""

    outcome: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    error: str | None = None


class Step7Runner(Protocol):
    """Interface implemented by local and production sandbox runners."""

    def attestation(self) -> dict[str, object]: ...

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: int,
        output_limit: int,
    ) -> RunnerResult: ...


def runner_attestation(
    *, runner_id: str, version: str, isolation: str, production_eligible: bool
) -> dict[str, object]:
    """Describe a runner execution context without asserting external trust."""
    value: dict[str, object] = {
        "id": runner_id,
        "version": version,
        "isolation": isolation,
        "production_eligible": production_eligible,
    }
    value["attestation_sha256"] = artifact_sha256(value)
    return value


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


class LocalSubprocessRunner:
    """Development-only local runner; its results never authorize a PR."""

    def attestation(self) -> dict[str, object]:
        return runner_attestation(
            runner_id="local-subprocess",
            version="0.1",
            isolation="local",
            production_eligible=False,
        )

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: int,
        output_limit: int,
    ) -> RunnerResult:
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            return RunnerResult("unavailable", None, b"", b"", str(exc))
        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        assert process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_group(process)
                    return RunnerResult(
                        "timeout",
                        None,
                        bytes(output["stdout"]),
                        bytes(output["stderr"]),
                    )
                events = selector.select(remaining)
                if not events:
                    _terminate_group(process)
                    return RunnerResult(
                        "timeout",
                        None,
                        bytes(output["stdout"]),
                        bytes(output["stderr"]),
                    )
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    stream = output[str(key.data)]
                    if len(stream) + len(chunk) > output_limit:
                        _terminate_group(process)
                        return RunnerResult(
                            "output_limit",
                            None,
                            bytes(output["stdout"]),
                            bytes(output["stderr"]),
                        )
                    stream.extend(chunk)
            return RunnerResult(
                "completed",
                process.wait(),
                bytes(output["stdout"]),
                bytes(output["stderr"]),
            )
        finally:
            selector.close()


__all__ = [
    "LocalSubprocessRunner",
    "RunnerResult",
    "Step7Runner",
    "runner_attestation",
]
