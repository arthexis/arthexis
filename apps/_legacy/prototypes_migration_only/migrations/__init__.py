"""Legacy prototypes migration package mapped to the historical chain."""

from pathlib import Path

_migrations_dir = Path(__file__).resolve().parents[3] / "prototypes" / "migrations"
__path__.append(str(_migrations_dir))
