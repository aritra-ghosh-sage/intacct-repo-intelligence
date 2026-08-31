"""Run the supported four-phase Greenfield Strands flow."""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_greenfield_strands import main


def run(argv: list[str] | None = None) -> int:
    """Run the flow and surface failures only; artifacts stay on disk."""

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = main(argv)
    if exit_code != 0:
        tail = captured.getvalue().strip().splitlines()
        if tail:
            print(tail[-1], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())
