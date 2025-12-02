#!/usr/bin/env python
"""Development maintenance tasks.

Ensures migrations are up to date and fixes inconsistent histories.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
import json
from collections import defaultdict
import tempfile
import hashlib
import time
from weakref import WeakKeyDictionary
from typing import TYPE_CHECKING, Iterable, Any
from datetime import datetime

import django
import importlib.util
from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections, connection, close_old_connections
from django.db.migrations.exceptions import (
    InconsistentMigrationHistory,
    InvalidBasesError,
)
from django.db.utils import OperationalError
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.loader import MigrationLoader
from django.core.serializers.base import DeserializationError
from utils.migration_branches import MissingBranchSplinterError


os.environ.setdefault("NET_MESSAGE_DISABLE_PROPAGATION", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db.models.signals import post_save
from apps.pages.models import Module, Landing, _create_landings
from apps.nodes.models import Node
from django.contrib.sites.models import Site
from django.contrib.auth import get_user_model

from apps.release.models import PackageRelease
from apps.sigils.sigil_builder import generate_model_sigils
from apps.locals.user_data import load_shared_user_fixtures, load_user_fixtures
from utils.env_refresh import unlink_sqlite_db as _unlink_sqlite_db
from django.utils import timezone
from django.utils.dateparse import parse_datetime


if TYPE_CHECKING:  # pragma: no cover - typing support
    from django.db.models import Model

_MODEL_SEED_FIELD_CACHE = WeakKeyDictionary()


def _model_defines_seed_flag(model: "type[Model]") -> bool:
    """Return whether *model* exposes the ``is_seed_data`` field.

    The result is cached per concrete model class to avoid recalculating the
    introspection for every object in the fixture stream.  A ``WeakKeyDictionary``
    keeps entries bounded to the lifetime of the model class so dynamically
    rendered models during the run remain isolated.
    """

    try:
        return _MODEL_SEED_FIELD_CACHE[model]
    except KeyError:
        has_field = any(field.name == "is_seed_data" for field in model._meta.fields)
        _MODEL_SEED_FIELD_CACHE[model] = has_field
        return has_field


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


def _load_fixture_with_retry(
    fixture: str,
    *,
    using_sqlite: bool,
    attempts: int = 5,
    base_delay: float = 0.2,
) -> None:
    """Load *fixture* while retrying sqlite lock conflicts."""

    for attempt in range(1, attempts + 1):
        try:
            call_command("load_user_data", fixture, verbosity=0)
            return
        except OperationalError as exc:
            if ("database is locked" not in str(exc).lower()) or not using_sqlite:
                raise
            if attempt == attempts:
                raise
            close_old_connections()
            delay = base_delay * attempt
            print(
                f"Database locked while loading {fixture}; retrying in {delay:.1f}s",
                flush=True,
            )
            time.sleep(delay)


def _assign_many_to_many(instance: "Model", field_name: str, value: Any) -> bool:
    manager = getattr(instance, field_name)
    if value is None or (
        isinstance(value, (list, tuple, set)) and not value
    ):
        manager.set([])
        return True

    related_manager = manager.model._default_manager
    if isinstance(value, (str, bytes)):
        iterable = [value]
    elif isinstance(value, (list, tuple, set)):
        iterable = list(value)
    else:
        iterable = [value]

    resolved = []
    for item in iterable:
        if isinstance(item, dict):
            try:
                resolved.append(related_manager.get(**item))
            except ObjectDoesNotExist:
                return False
            continue
        try:
            if isinstance(item, (list, tuple)):
                resolved.append(related_manager.get_by_natural_key(*item))
            else:
                resolved.append(related_manager.get_by_natural_key(item))
            continue
        except (AttributeError, ObjectDoesNotExist, TypeError):
            try:
                resolved.append(related_manager.get(pk=item))
            except (ObjectDoesNotExist, TypeError, ValueError):
                return False
    manager.set(resolved)
    return True


def _fixture_sort_key(name: str) -> tuple[int, str]:
    """Sort fixtures to satisfy foreign key dependencies."""
    filename = Path(name).name
    if filename.startswith("group__"):
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


def _preferred_site_domain(domains: Iterable[str]) -> str | None:
    """Return the preferred ``Site`` domain from *domains*.

    Preference follows the order defined in ``settings.ALLOWED_HOSTS`` while
    skipping wildcard and network entries.  If none of those hosts appear in
    the fixture set, fall back to the lexicographically smallest domain to
    guarantee determinism.
    """

    if not domains:
        return None

    normalized: list[str] = []
    for domain in domains:
        if isinstance(domain, str):
            candidate = domain.strip()
            if candidate:
                normalized.append(candidate)

    if not normalized:
        return None

    allowed_hosts = getattr(settings, "ALLOWED_HOSTS", [])
    for host in allowed_hosts:
        if not isinstance(host, str):
            continue
        candidate = host.strip()
        if not candidate or candidate.startswith("*") or "/" in candidate:
            continue
        if candidate == "localhost" and "127.0.0.1" in normalized:
            continue
        if candidate in normalized:
            return candidate

    return sorted(normalized)[0]


def _migration_hash(app_labels: list[str]) -> str:
    """Return an md5 hash of all migration files for the given apps."""
    md5 = hashlib.md5(usedforsecurity=False)
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


def _fixtures_hash(fixtures: Iterable[str]) -> str:
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

    base_dir = Path(settings.BASE_DIR)
    locks_dir = base_dir / ".locks"
    locks_dir.mkdir(exist_ok=True)
    local_apps = _local_app_labels()

    _remove_integrator_from_auth_migration()

    try:
        call_command("makemigrations", *local_apps, interactive=False)
    except CommandError as exc:
        call_command("makemigrations", *local_apps, merge=True, interactive=False)
    except InconsistentMigrationHistory:
        if using_sqlite:
            _unlink_sqlite_db(Path(default_db["NAME"]))
            call_command("makemigrations", *local_apps, interactive=False)
        else:  # pragma: no cover - unreachable in sqlite
            raise

    # Compute migrations hash and compare with stored value
    hash_file = locks_dir / "migrations.md5"
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
        except MissingBranchSplinterError as exc:
            print(
                "Detected a retroactively edited migration branch that this database "
                "skipped.\n"
                f"{exc}\n"
                "Manually recreate the database or roll it back to the splinter "
                "migration before retrying the installation.",
                flush=True,
            )
            raise
        except InconsistentMigrationHistory:
            call_command("reset_ocpp_migrations")
            call_command("migrate", interactive=False)
        except InvalidBasesError:
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
    SigilRoot = apps.get_model("sigils", "SigilRoot")
    SigilRoot.objects.all().delete()

    # Track Site entries provided via fixtures so we can update them without
    # disturbing operator-managed records.
    Site = apps.get_model("sites", "Site")

    # Ensure Application and Module entries exist for local apps before loading
    # fixtures that reference them.
    call_command("register_site_apps")

    fixture_hash_file = locks_dir / "fixtures.md5"
    fixtures = _fixture_files()
    fixture_hash = _fixtures_hash(fixtures) if fixtures else ""
    stored_fixture_hash = (
        fixture_hash_file.read_text().strip() if fixture_hash_file.exists() else ""
    )
    migrations_changed = stored_hash != new_hash
    should_load_fixtures = fixtures and (
        clean or migrations_changed or fixture_hash != stored_fixture_hash
    )

    if should_load_fixtures:
        fixtures.sort(key=_fixture_sort_key)
        with tempfile.TemporaryDirectory() as tmpdir:
            patched: dict[int, list[str]] = {}
            user_pk_map: dict[int, int] = {}
            model_counts: dict[str, int] = defaultdict(int)
            site_fixture_defaults: dict[str, dict] = {}
            pending_user_m2m: dict[int, list[tuple[str, Any]]] = defaultdict(list)
            for name in fixtures:
                priority, _ = _fixture_sort_key(name)
                source = Path(settings.BASE_DIR, name)
                with source.open() as f:
                    data = json.load(f)
                patched_data: list[dict] = []
                modified = False
                for obj in data:
                    model_label = obj.get("model", "")
                    try:
                        model = apps.get_model(model_label)
                    except LookupError:
                        modified = True
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
                            m2m_updates: list[tuple[str, Any]] = []
                            for field_name, value in obj.get("fields", {}).items():
                                try:
                                    field_object = model._meta.get_field(field_name)
                                except FieldDoesNotExist:
                                    setattr(existing, field_name, value)
                                    continue
                                if field_object.many_to_many:
                                    m2m_updates.append((field_name, value))
                                else:
                                    setattr(existing, field_name, value)
                            existing.save()
                            for field_name, value in m2m_updates:
                                if not _assign_many_to_many(existing, field_name, value):
                                    pending_user_m2m[existing.pk].append((field_name, value))
                            modified = True
                            continue
                    fields = obj.setdefault("fields", {})
                    if "user" in fields and isinstance(fields["user"], int):
                        original_user = fields["user"]
                        mapped_user = user_pk_map.get(original_user, original_user)
                        if mapped_user != original_user:
                            fields["user"] = mapped_user
                            modified = True
                    if model_label == "sigils.sigilroot":
                        content_type = fields.get("content_type")
                        app_label: str | None = None
                        if isinstance(content_type, (list, tuple)) and content_type:
                            app_label = content_type[0]
                        elif isinstance(content_type, dict):
                            app_label = content_type.get("app_label")
                        if app_label:
                            try:
                                apps.get_app_config(app_label)
                            except LookupError:
                                prefix = fields.get("prefix", "?")
                                print(
                                    f"Skipping SigilRoot '{prefix}' (missing app '{app_label}')"
                                )
                                modified = True
                                continue
                    if model is Site:
                        domain = fields.get("domain")
                        if domain:
                            defaults = dict(fields)
                            Site.objects.update_or_create(
                                domain=domain,
                                defaults=defaults,
                            )
                            site_fixture_defaults[domain] = defaults
                            model_counts[model._meta.label] += 1
                        modified = True
                        continue
                    if model is PackageRelease:
                        version = obj.get("fields", {}).get("version")
                        if (
                            version
                            and PackageRelease.objects.filter(version=version).exists()
                        ):
                            modified = True
                            continue
                    defines_seed_flag = _model_defines_seed_flag(model)
                    has_seed_field = any(
                        f.name == "is_seed_data" for f in model._meta.fields
                    )
                    if (defines_seed_flag or has_seed_field) and fields.get(
                        "is_seed_data"
                    ) is not True:
                        fields["is_seed_data"] = True
                        modified = True
                    patched_data.append(obj)
                    model_counts[model._meta.label] += 1
                if modified:
                    dest = Path(tmpdir, Path(name).name)
                    with dest.open("w") as f:
                        json.dump(patched_data, f)
                    target = str(dest)
                else:
                    target = str(source)
                if patched_data:
                    patched.setdefault(priority, []).append(target)
            post_save.disconnect(_create_landings, sender=Module)
            try:
                for priority in sorted(patched):
                    for fixture in patched[priority]:
                        try:
                            _load_fixture_with_retry(
                                fixture,
                                using_sqlite=using_sqlite,
                            )
                        except DeserializationError as exc:
                            print(f"Skipping fixture {fixture} due to: {exc}")
                        else:
                            print(".", end="", flush=True)
                if pending_user_m2m:
                    for user_pk, assignments in pending_user_m2m.items():
                        user = get_user_model().objects.filter(pk=user_pk).first()
                        if not user:
                            continue
                        for field_name, value in assignments:
                            if not _assign_many_to_many(user, field_name, value):
                                raise ValueError(
                                    f"Unable to resolve many-to-many values for user {user_pk}"
                                )
                for module in Module.objects.all():
                    module.create_landings()

                if site_fixture_defaults:
                    preferred = _preferred_site_domain(site_fixture_defaults)
                    if preferred:
                        defaults = dict(site_fixture_defaults[preferred])
                        defaults["domain"] = preferred
                        site_id = getattr(settings, "SITE_ID", 1)
                        existing = Site.objects.filter(pk=site_id).first()
                        Site.objects.filter(domain=preferred).exclude(pk=site_id).delete()
                        if existing:
                            for field, value in defaults.items():
                                setattr(existing, field, value)
                            existing.save(update_fields=list(defaults.keys()))
                        else:
                            Site.objects.update_or_create(
                                pk=site_id,
                                defaults=defaults,
                            )
                    Site.objects.clear_cache()

                if model_counts:
                    print()
                    for label, count in sorted(model_counts.items()):
                        print(f"{label}: {count}")
            finally:
                post_save.connect(_create_landings, sender=Module)

        # Refresh seed flags for Landing entries created during fixture loading.
        Landing.objects.update(is_seed_data=True)

        # Load shared fixtures once before personal data
        load_shared_user_fixtures(force=True)

        # Load personal user data fixtures last
        for user in get_user_model().objects.all():
            load_user_fixtures(user)

        # Recreate any missing SigilRoots after loading fixtures
        generate_model_sigils()

        fixture_hash_file.write_text(fixture_hash)
    elif fixtures:
        print("Fixtures unchanged; skipping reload.")

    # Ensure current node is registered or updated
    node, _ = Node.register_current(notify_peers=False)

    control_lock = Path(settings.BASE_DIR) / ".locks" / "control.lck"
    if control_lock.exists():
        Site.objects.update_or_create(
            domain=node.public_endpoint,
            defaults={"name": "Control"},
        )

    # Update the migrations hash file after a successful run.
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
