"""Backward-compatible alias for the `solve` command."""

from __future__ import annotations

from .solve import Command as SolveCommand


class Command(SolveCommand):
    """Alias entrypoint for existing automation using run_sigil_script."""

    pass
