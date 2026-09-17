"""Fixture discovery, hashing, ordering, and loading helpers for env refresh."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.serializers.base import DeserializationError
from django.db import close_old_connections
from django.db.utils import OperationalError


def close_old_connections_safely() -> None:
    """Close stale Django DB connections without requiring active DB fixtures."""

    try:
        close_old_connections()
    except RuntimeError as exc:
        message = str(exc).lower()
        if "database access not allowed" in message:
            return
        if "database access is not allowed" in message:
            return
        raise


def fixture_files() -> list[str]:
    """Return all JSON fixtures in the project."""

    base_dir = Path(settings.BASE_DIR)
    fixtures = [
        str(path.relative_to(base_dir)) for path in base_dir.glob("**/fixtures/*.json")
    ]
    return sorted(fixtures)


def fixture_mtime_cache(fixtures: Iterable[str]) -> dict[str, float]:
    """Return modification times for *fixtures* relative to ``BASE_DIR``."""

    base_dir = Path(settings.BASE_DIR)
    cache: dict[str, float] = {}
    for fixture in fixtures:
        path = base_dir / fixture
        try:
            cache[fixture] = path.stat().st_mtime
        except OSError:
            continue
    return cache


def fixture_tables(fixtures: list[str]) -> set[str]:
    """Return database table names referenced by *fixtures*.

    Fixtures that target models missing from the current apps registry are
    ignored so optional/removed fixtures do not block the refresh process.
    """

    required_tables: set[str] = set()
    base_dir = Path(settings.BASE_DIR)
    for name in fixtures:
        source = base_dir / name
        try:
            with source.open() as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        for obj in data:
            model_label = obj.get("model", "")
            try:
                model = apps.get_model(model_label)
            except LookupError:
                continue
            required_tables.add(model._meta.db_table)
    return required_tables


def fixture_load_prescan(fixtures: Iterable[str]) -> tuple[set[str], dict[int, int]]:
    """Collect cross-fixture context needed before scoped fixture loading."""

    pending_role_names: set[str] = set()
    user_pk_map: dict[int, int] = {}
    UserModel = get_user_model()

    for name in fixtures:
        source = Path(settings.BASE_DIR, name)
        try:
            with source.open() as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        for obj in data:
            model_label = obj.get("model", "")
            if model_label == "nodes.noderole":
                role_name = obj.get("fields", {}).get("name")
                if isinstance(role_name, str) and role_name:
                    pending_role_names.add(role_name)
            try:
                model = apps.get_model(model_label)
            except LookupError:
                continue
            if model is UserModel:
                username = obj.get("fields", {}).get("username")
                existing = None
                if username:
                    existing = UserModel.objects.filter(username=username).first()
                if existing and obj.get("pk") is not None:
                    user_pk_map[obj.get("pk")] = existing.pk

    return pending_role_names, user_pk_map


def load_fixture_with_retry(
    fixture: str,
    *,
    using_sqlite: bool,
    attempts: int = 12,
    base_delay: float = 0.5,
) -> None:
    """Load *fixture* while retrying sqlite lock conflicts."""

    for attempt in range(1, attempts + 1):
        try:
            call_command("loaddata", fixture, verbosity=0)
            return
        except OperationalError as exc:
            if ("database is locked" not in str(exc).lower()) or not using_sqlite:
                raise
            if attempt == attempts:
                raise
            close_old_connections_safely()
            delay = base_delay * attempt
            print(
                f"Database locked while loading {fixture}; retrying in {delay:.1f}s",
                flush=True,
            )
            time.sleep(delay)


def fixture_sort_key(name: str) -> tuple[int, str]:
    """Sort fixtures to satisfy foreign key dependencies."""

    filename = Path(name).name
    if filename.startswith("locale__languages"):
        priority = -2
    elif filename.startswith("group__"):
        priority = -1
    elif filename.startswith("security_groups__"):
        priority = -1
    elif filename.startswith("users__"):
        priority = 0
    elif "__application_" in filename or "__noderole_" in filename:
        priority = 1
    elif "__module_" in filename:
        priority = 2
    elif filename.startswith("developerarticle__"):
        priority = 3
    elif "__landing_" in filename:
        priority = 3
    else:
        priority = 4
    return (priority, filename)


def load_fixtures_with_deferred_retry(
    patched: dict[int, list[str]],
    *,
    using_sqlite: bool,
) -> None:
    """Load fixture files by priority and retry deferred deserialization failures.

    The first pass preserves the existing load order. Any fixture that fails due
    to unresolved model references is retried after all other fixtures have had a
    chance to populate dependencies. Deferred fixtures keep retrying while each
    pass makes progress, which handles chained dependencies deterministically.
    """

    deferred_fixtures: list[tuple[str, DeserializationError]] = []
    for priority in sorted(patched):
        for fixture in patched[priority]:
            try:
                load_fixture_with_retry(
                    fixture,
                    using_sqlite=using_sqlite,
                )
            except DeserializationError as exc:
                deferred_fixtures.append((fixture, exc))
            else:
                print(".", end="", flush=True)

    if not deferred_fixtures:
        return

    while deferred_fixtures:
        remaining: list[tuple[str, DeserializationError]] = []
        progress_made = False
        for fixture, _ in deferred_fixtures:
            try:
                load_fixture_with_retry(
                    fixture,
                    using_sqlite=using_sqlite,
                )
            except DeserializationError as exc:
                remaining.append((fixture, exc))
            else:
                progress_made = True
                print(".", end="", flush=True)

        if not progress_made:
            for fixture, exc in remaining:
                print(f"Skipping fixture {fixture} due to: {exc}")
            return

        deferred_fixtures = remaining


def fixtures_hash(fixtures: Iterable[str]) -> str:
    """Return an md5 hash of the provided fixture files."""

    base_dir = Path(settings.BASE_DIR)
    digest = hashlib.md5(usedforsecurity=False)
    for fixture in sorted(fixtures):
        path = base_dir / fixture
        try:
            digest.update(str(path.relative_to(base_dir)).encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def fixture_hashes_by_app(fixtures: Iterable[str]) -> dict[str, str]:
    """Return md5 hashes of fixtures grouped by app label."""

    base_dir = Path(settings.BASE_DIR)
    buckets: dict[str, hashlib._Hash] = {}

    for fixture in sorted(fixtures):
        path = base_dir / fixture
        parts = path.relative_to(base_dir).parts
        label = parts[1] if len(parts) >= 3 and parts[0] == "apps" else "global"
        digest = buckets.setdefault(label, hashlib.md5(usedforsecurity=False))
        try:
            digest.update(str(path.relative_to(base_dir)).encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            continue

    return {label: digest.hexdigest() for label, digest in buckets.items()}
