"""Deprecated compatibility shim for the supported Greenfield runner."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_greenfield import main

if __name__ == "__main__":
    warnings.warn(
        "scripts/run_greenfield_codex.py is deprecated; use scripts/run_greenfield.py",
        DeprecationWarning,
        stacklevel=1,
    )
    raise SystemExit(main())
