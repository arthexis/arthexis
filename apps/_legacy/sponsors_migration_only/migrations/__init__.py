"""Legacy sponsors migration package mapped to the historical migration chain."""

from pathlib import Path

_migrations_dir = Path(__file__).resolve().parents[3] / "sponsors" / "migrations"
__path__.append(str(_migrations_dir))
