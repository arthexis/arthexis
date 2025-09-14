#!/usr/bin/env python
"""Development maintenance tasks.

Ensures migrations are up to date and fixes inconsistent histories.
"""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
import json
import tempfile
import hashlib
import time
import re

import django
import importlib.util
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections, connection
from django.db.migrations.exceptions import (
    InconsistentMigrationHistory,
    InvalidBasesError,
)
from django.db.utils import OperationalError
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.loader import MigrationLoader
from django.core.serializers.base import DeserializationError


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db.models.signals import post_save
from pages.models import Module, Landing, _create_landings
from nodes.models import Node
from django.contrib.sites.models import Site
from django.contrib.auth import get_user_model

from core.models import PackageRelease
from core.sigil_builder import generate_model_sigils


def _unlink_sqlite_db(path: Path) -> None:
    """Close database connections and remove only the SQLite DB file."""
    connections.close_all()
    try:
        base_dir = Path(settings.BASE_DIR).resolve()
    except Exception:
        base_dir = path.parent.resolve()
    path = path.resolve()
    try:
        path.relative_to(base_dir)
    except ValueError:
        raise RuntimeError(f"Refusing to delete database outside {base_dir}: {path}")
    if not re.fullmatch(r"db(?:_[0-9a-f]{6})?\.sqlite3", path.name):
        raise RuntimeError(f"Refusing to delete unexpected database file: {path.name}")
    for _ in range(5):
        try:
            path.unlink(missing_ok=True)
            break
        except PermissionError:
            time.sleep(0.1)
            connections.close_all()


def _local_app_labels() -> list[str]:
    base_dir = Path(settings.BASE_DIR)
    labels: list[str] = []
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path)
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            continue
        labels.append(app_config.label)
    return labels


def _fixture_files() -> list[str]:
    """Return all JSON fixtures in the project."""
    base_dir = Path(settings.BASE_DIR)
    fixtures = [
        str(path.relative_to(base_dir)) for path in base_dir.glob("**/fixtures/*.json")
    ]
    return sorted(fixtures)


def _fixture_sort_key(name: str) -> tuple[int, str]:
    """Sort fixtures to satisfy foreign key dependencies."""
    filename = Path(name).name
    if filename.startswith("users__"):
        priority = 0
    elif "__application_" in filename or "__noderole_" in filename:
        priority = 1
    elif "__module_" in filename:
        priority = 2
    elif "__landing_" in filename:
        priority = 3
    else:
        priority = 4
    return (priority, filename)


def _fixture_hash(files: list[str]) -> str:
    md5 = hashlib.md5()
    for name in files:
        md5.update(Path(settings.BASE_DIR, name).read_bytes())
    return md5.hexdigest()


def _write_fixture_hash(value: str) -> None:
    """Persist the fixtures hash to disk."""
    (Path(settings.BASE_DIR) / "fixtures.md5").write_text(value)


def _migration_hash(app_labels: list[str]) -> str:
    """Return an md5 hash of all migration files for the given apps."""
    md5 = hashlib.md5()
    for label in app_labels:
        try:
            app_config = apps.get_app_config(label)
        except LookupError:  # pragma: no cover - defensive
            continue
        migrations_dir = Path(app_config.path) / "migrations"
        if not migrations_dir.is_dir():
            continue
        for path in sorted(migrations_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            md5.update(path.read_bytes())
    return md5.hexdigest()


def _remove_integrator_from_auth_migration() -> None:
    """Strip lingering integrator imports from Django's auth migration."""
    spec = importlib.util.find_spec("django.contrib.auth.migrations.0013_userproxy")
    if not spec or not spec.origin:
        return
    path = Path(spec.origin)
    try:
        content = path.read_text()
    except OSError:
        return
    if "integrator" not in content:
        return
    patched = "\n".join(
        line for line in content.splitlines() if "integrator" not in line
    )
    path.write_text(patched + ("\n" if not patched.endswith("\n") else ""))


def run_database_tasks(*, latest: bool = False, clean: bool = False) -> None:
    """Run all database related maintenance steps."""
    default_db = settings.DATABASES["default"]
    using_sqlite = default_db["ENGINE"] == "django.db.backends.sqlite3"

    local_apps = _local_app_labels()

    _remove_integrator_from_auth_migration()

    try:
        call_command("makemigrations", *local_apps, interactive=False)
    except CommandError:
        call_command("makemigrations", *local_apps, merge=True, interactive=False)
    except InconsistentMigrationHistory:
        if using_sqlite:
            _unlink_sqlite_db(Path(default_db["NAME"]))
            call_command("makemigrations", *local_apps, interactive=False)
        else:  # pragma: no cover - unreachable in sqlite
            raise

    # Compute migrations hash and compare with stored value
    hash_file = Path(settings.BASE_DIR) / "migrations.md5"
    new_hash = _migration_hash(local_apps)
    stored_hash = hash_file.read_text().strip() if hash_file.exists() else ""

    if clean:
        if stored_hash and stored_hash != new_hash:
            if using_sqlite:
                _unlink_sqlite_db(Path(default_db["NAME"]))
            else:  # pragma: no cover - unreachable in sqlite
                for label in reversed(local_apps):
                    call_command("migrate", label, "zero", interactive=False)
        else:
            try:
                recorder = MigrationRecorder(connection)
                loader = MigrationLoader(connection)
            except OperationalError:
                recorder = loader = None
            if recorder and loader:
                for label in local_apps:
                    try:
                        qs = recorder.migration_qs.filter(app=label).order_by(
                            "-applied"
                        )
                        if qs.exists():
                            last = qs.first().name
                            node = loader.graph.node_map.get((label, last))
                            parents = list(node.parents) if node else []
                            prev = parents[0][1] if parents else "zero"
                            call_command("migrate", label, prev, interactive=False)
                    except OperationalError:
                        continue

    if not connection.in_atomic_block:
        try:
            call_command("migrate", interactive=False)
        except InconsistentMigrationHistory:
            call_command("reset_ocpp_migrations")
            call_command("migrate", interactive=False)
        except InvalidBasesError as exc:
            if "post_office.WorkgroupNewsArticle" in str(exc):
                call_command(
                    "migrate", "post_office", "0014", fake=True, interactive=False
                )
                call_command("migrate", interactive=False)
            else:
                raise
        except OperationalError as exc:
            if using_sqlite:
                _unlink_sqlite_db(Path(default_db["NAME"]))
                call_command("migrate", interactive=False)
            else:  # pragma: no cover - unreachable in sqlite
                try:
                    import psycopg
                    from psycopg import sql

                    params = {
                        "dbname": "postgres",
                        "user": default_db.get("USER", ""),
                        "password": default_db.get("PASSWORD", ""),
                        "host": default_db.get("HOST", ""),
                        "port": default_db.get("PORT", ""),
                    }
                    with psycopg.connect(**params, autocommit=True) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                sql.SQL("CREATE DATABASE {}").format(
                                    sql.Identifier(default_db["NAME"])
                                )
                            )
                    call_command("migrate", interactive=False)
                except Exception:
                    raise exc

    # Remove auto-generated SigilRoot entries so fixtures define prefixes
    SigilRoot = apps.get_model("core", "SigilRoot")
    SigilRoot.objects.all().delete()

    # Remove existing Site entries to avoid duplicate domain constraints
    Site = apps.get_model("sites", "Site")
    Site.objects.all().delete()

    fixtures = _fixture_files()
    fixture_hash = _fixture_hash(fixtures)
    if fixtures:
        fixtures.sort(key=_fixture_sort_key)
        with tempfile.TemporaryDirectory() as tmpdir:
            patched: dict[int, list[str]] = {}
            user_pk_map: dict[int, int] = {}
            for name in fixtures:
                priority, _ = _fixture_sort_key(name)
                source = Path(settings.BASE_DIR, name)
                with source.open() as f:
                    data = json.load(f)
                patched_data: list[dict] = []
                for obj in data:
                    model_label = obj.get("model", "")
                    try:
                        model = apps.get_model(model_label)
                    except LookupError:
                        continue
                    # Update existing users instead of loading duplicates and
                    # record their primary key mapping for later references.
                    if model is get_user_model():
                        username = obj.get("fields", {}).get("username")
                        existing = None
                        if username:
                            existing = (
                                get_user_model()
                                .objects.filter(username=username)
                                .first()
                            )
                        if existing:
                            user_pk_map[obj.get("pk")] = existing.pk
                            for field, value in obj.get("fields", {}).items():
                                setattr(existing, field, value)
                            existing.save()
                            continue
                    fields = obj.get("fields", {})
                    if "user" in fields and isinstance(fields["user"], int):
                        fields["user"] = user_pk_map.get(fields["user"], fields["user"])
                    if model is PackageRelease:
                        version = obj.get("fields", {}).get("version")
                        if (
                            version
                            and PackageRelease.objects.filter(version=version).exists()
                        ):
                            continue
                    if any(f.name == "is_seed_data" for f in model._meta.fields):
                        obj.setdefault("fields", {})["is_seed_data"] = True
                    patched_data.append(obj)
                dest = Path(tmpdir, Path(name).name)
                with dest.open("w") as f:
                    json.dump(patched_data, f)
                if patched_data:
                    patched.setdefault(priority, []).append(str(dest))
            post_save.disconnect(_create_landings, sender=Module)
            try:
                for priority in sorted(patched):
                    for fixture in patched[priority]:
                        try:
                            try:
                                call_command(
                                    "loaddata",
                                    fixture,
                                    natural_foreign=True,
                                    natural_primary=True,
                                )
                            except TypeError:
                                call_command("loaddata", fixture)
                        except DeserializationError as exc:
                            print(f"Skipping fixture {fixture} due to: {exc}")
                for module in Module.objects.all():
                    module.create_landings()
                Landing.objects.update(is_seed_data=True)
            finally:
                post_save.connect(_create_landings, sender=Module)

    # Ensure Application and Module entries exist for local apps
    call_command("register_site_apps")
    Landing.objects.update(is_seed_data=True)

    # Ensure current node is registered or updated
    node, _ = Node.register_current()

    control_lock = Path(settings.BASE_DIR) / "locks" / "control.lck"
    if control_lock.exists():
        Site.objects.update_or_create(
            domain=node.public_endpoint,
            defaults={"name": "Control"},
        )

    # Load personal user data fixtures last

    # Recreate any missing SigilRoots after loading fixtures
    generate_model_sigils()

    # Update the fixtures and migrations hash files after a successful run.
    _write_fixture_hash(fixture_hash)
    hash_file.write_text(new_hash)


TASKS = {"database": run_database_tasks}


def main(
    selected: list[str] | None = None, *, latest: bool = False, clean: bool = False
) -> None:
    """Run the selected maintenance tasks."""
    to_run = selected or list(TASKS)
    for name in to_run:
        TASKS[name](latest=latest, clean=clean)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Development maintenance tasks")
    parser.add_argument("tasks", nargs="*", choices=TASKS.keys(), help="Tasks to run")
    parser.add_argument(
        "--latest", action="store_true", help="Force rebuild if migrations changed"
    )
    parser.add_argument(
        "--clean", action="store_true", help="Reset database before applying migrations"
    )
    args = parser.parse_args()
    main(args.tasks, latest=args.latest, clean=args.clean)
