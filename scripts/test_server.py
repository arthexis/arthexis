#!/usr/bin/env python3
"""Compatibility shim for the VS Code test launcher entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from utils.devtools.test_server import main


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
