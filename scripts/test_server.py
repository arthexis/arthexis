#!/usr/bin/env python3
"""Compatibility shim for the VS Code test server entrypoint."""

from __future__ import annotations

from apps.vscode.test_server import main


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
