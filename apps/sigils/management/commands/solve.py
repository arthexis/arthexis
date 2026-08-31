"""Short command for executing sigil expressions and .artx scripts."""

from __future__ import annotations

from ._solve_base import BaseSolveCommand


class Command(BaseSolveCommand):
    """Run one sigil expression or LET/EMIT script with no cache by default."""

    help = "Solve one sigil expression or a LET/EMIT .artx script."
    default_use_cache = False
