"""Synonym command for sigil solve execution with cache-enabled defaults."""

from __future__ import annotations

from ._solve_base import BaseSolveCommand


class Command(BaseSolveCommand):
    """Resolve sigil expressions/scripts and cache identical inputs by default."""

    help = "Resolve one sigil expression or a LET/EMIT .artx script."
    default_use_cache = True
