#!/usr/bin/env python
"""Development maintenance tasks.

Ensures migrations are up to date and fixes inconsistent histories.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import json
import tempfile
import hashlib

import django
import importlib.util
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections, connection
from django.db.migrations.exceptions import InconsistentMigrationHistory
from django.db.utils import OperationalError


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db.models.signals import post_save
from pages.models import Module, Landing, _create_landings
from nodes.models import Node
from django.contrib.sites.models import Site
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model

from core.user_data import UserDatum
from core.models import Package, PackageRelease
from utils import revision as revision_utils


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
        str(path.relative_to(base_dir))
        for path in base_dir.glob("**/fixtures/*.json")
    ]
    return sorted(fixtures)


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


def _refresh_next_release(version: str = "0.1.2") -> None:
    """Recreate the pending package release for the given version."""
    package = Package.objects.first()
    if not package:
        return
    PackageRelease.all_objects.filter(version=version).delete()
    PackageRelease.objects.create(
        package=package,
        profile=package.release_manager,
        version=version,
        revision=revision_utils.get_revision(),
        is_seed_data=True,
    )


def run_database_tasks(*, latest: bool = False) -> None:
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
            connections.close_all()
            Path(default_db["NAME"]).unlink(missing_ok=True)
            call_command("makemigrations", *local_apps, interactive=False)
        else:  # pragma: no cover - unreachable in sqlite
            raise

    # Compute migrations hash and compare with stored value
    hash_file = Path(settings.BASE_DIR) / "migrations.md5"
    new_hash = _migration_hash(local_apps)
    stored_hash = hash_file.read_text().strip() if hash_file.exists() else ""

    if latest and stored_hash and stored_hash != new_hash:
        if using_sqlite:
            connections.close_all()
            Path(default_db["NAME"]).unlink(missing_ok=True)
        else:  # pragma: no cover - unreachable in sqlite
            for label in reversed(local_apps):
                call_command("migrate", label, "zero", interactive=False)

    if not connection.in_atomic_block:
        try:
            call_command("migrate", interactive=False)
        except InconsistentMigrationHistory:
            call_command("reset_ocpp_migrations")
            call_command("migrate", interactive=False)
        except OperationalError as exc:
            if using_sqlite:
                connections.close_all()
                Path(default_db["NAME"]).unlink(missing_ok=True)
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
                                sql.SQL("CREATE DATABASE {}" ).format(
                                    sql.Identifier(default_db["NAME"])
                                )
                            )
                    call_command("migrate", interactive=False)
                except Exception:
                    raise exc

    fixtures = _fixture_files()
    if fixtures:
        with tempfile.TemporaryDirectory() as tmpdir:
            patched: list[str] = []
            for name in fixtures:
                source = Path(settings.BASE_DIR, name)
                with source.open() as f:
                    data = json.load(f)
                for obj in data:
                    model_label = obj.get("model", "")
                    try:
                        model = apps.get_model(model_label)
                    except LookupError:
                        continue
                    if any(f.name == "is_seed_data" for f in model._meta.fields):
                        obj.setdefault("fields", {})["is_seed_data"] = True
                dest = Path(tmpdir, Path(name).name)
                with dest.open("w") as f:
                    json.dump(data, f)
                patched.append(str(dest))
            post_save.disconnect(_create_landings, sender=Module)
            try:
                call_command("loaddata", *patched)
                for module in Module.objects.all():
                    module.create_landings()
                Landing.objects.update(is_seed_data=True)
            finally:
                post_save.connect(_create_landings, sender=Module)

    # Ensure Application and Module entries exist for local apps
    call_command("register_site_apps")
    Landing.objects.update(is_seed_data=True)

    # Recreate the upcoming package release to track latest revision
    _refresh_next_release()

    # Ensure current node is registered or updated
    node, _ = Node.register_current()

    control_lock = Path(settings.BASE_DIR) / "locks" / "control.lck"
    if control_lock.exists():
        Site.objects.update_or_create(
            domain=node.public_endpoint,
            defaults={"name": "Control"},
        )

    # Load personal user data fixtures last
    data_dir = Path(settings.BASE_DIR) / "data"
    if data_dir.is_dir():
        personal = sorted(data_dir.glob("*.json"))
        if personal:
            User = get_user_model()
            for p in personal:
                try:
                    user_id = int(p.stem.split("_", 1)[0])
                    User.objects.get_or_create(
                        pk=user_id, defaults={"username": f"user{user_id}"}
                    )
                except Exception:
                    pass
            call_command("loaddata", *[str(p) for p in personal])
            for p in personal:
                try:
                    user_id, app_label, model, obj_id = p.stem.split("_", 3)
                    ct = ContentType.objects.get_by_natural_key(app_label, model)
                    UserDatum.objects.get_or_create(
                        user_id=int(user_id), content_type=ct, object_id=int(obj_id)
                    )
                except Exception:
                    continue

    # Update migrations hash file after successful run
    hash_file.write_text(new_hash)


TASKS = {"database": run_database_tasks}


def main(selected: list[str] | None = None, *, latest: bool = False) -> None:
    """Run the selected maintenance tasks."""
    to_run = selected or list(TASKS)
    for name in to_run:
        TASKS[name](latest=latest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Development maintenance tasks")
    parser.add_argument(
        "tasks", nargs="*", choices=TASKS.keys(), help="Tasks to run"
    )
    parser.add_argument(
        "--latest", action="store_true", help="Force rebuild if migrations changed"
    )
    args = parser.parse_args()
    main(args.tasks, latest=args.latest)

