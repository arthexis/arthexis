from __future__ import annotations

import hashlib
from importlib import import_module
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parents[1]
LOCK_FILE = BASE_DIR / "locks" / "db-revision.lck"


def _migration_paths(apps: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for app in apps:
        try:
            module = import_module(app)
        except Exception:
            continue
        app_path = Path(module.__file__).resolve().parent
        migrations_dir = app_path / "migrations"
        if not migrations_dir.is_dir():
            continue
        for path in sorted(migrations_dir.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            paths.append(path)
    return paths


def compute_hash(apps: Iterable[str]) -> str:
    md5 = hashlib.md5()
    for path in _migration_paths(apps):
        md5.update(path.read_bytes())
    return md5.hexdigest()


def get_db_revision(apps: Iterable[str]) -> str:
    """Return current DB revision and persist it to the lockfile."""
    hash_value = compute_hash(apps)
    existing = LOCK_FILE.read_text().strip() if LOCK_FILE.exists() else ""
    if existing != hash_value:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(hash_value)
    return hash_value
