from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import DatabaseConfig


class Command(BaseCommand):
    """Validate Postgres configuration and optionally migrate SQLite data."""

    help = "Validate Postgres status/configuration and optionally migrate from SQLite"

    def add_arguments(self, parser):
        """Register command-line arguments."""

        parser.add_argument("--host", default="localhost", help="Postgres host")
        parser.add_argument("--port", default="5432", help="Postgres port")
        parser.add_argument("--name", default="postgres", help="Postgres database name")
        parser.add_argument("--user", default="postgres", help="Postgres database user")
        parser.add_argument("--password", default="", help="Postgres database password")
        parser.add_argument(
            "--migrate",
            action="store_true",
            help="Attempt to migrate data from SQLite to PostgreSQL",
        )
        parser.add_argument(
            "--sqlite-path",
            default="",
            help="Path to the source SQLite database (default: BASE_DIR/db.sqlite3)",
        )
        parser.add_argument(
            "--no-store",
            action="store_true",
            help="Do not persist lock file and DatabaseConfig row",
        )

    def handle(self, *args, **options):  # type: ignore[override]
        """Execute command workflow."""

        config = {
            "backend": "postgres",
            "host": str(options["host"]),
            "port": str(options["port"]),
            "name": str(options["name"]),
            "user": str(options["user"]),
            "password": str(options["password"]),
        }

        if not options["no_store"]:
            self._write_lock(config)
            self._upsert_runtime_config(config)

        ok, message = self._validate_connection(config)
        self.stdout.write(f"Postgres connection: {'OK' if ok else 'FAILED'}")
        self.stdout.write(f"Details: {message}")

        if not options["no_store"]:
            self._record_status(config, ok=ok, error="" if ok else message)

        if options["migrate"]:
            if not ok:
                raise CommandError("Cannot migrate because Postgres connectivity failed.")
            migrated = self._migrate_sqlite_to_postgres(
                sqlite_path=options.get("sqlite_path") or "", postgres_config=config
            )
            if migrated:
                self.stdout.write(self.style.SUCCESS("SQLite data migration completed."))
            else:
                self.stdout.write(
                    self.style.WARNING("SQLite migration skipped (source database missing).")
                )

    def _validate_connection(self, config: dict[str, str]) -> tuple[bool, str]:
        """Validate connectivity to PostgreSQL using psycopg."""

        try:
            import psycopg
        except Exception as exc:  # pragma: no cover - environment dependent
            return False, f"psycopg import failed: {exc}"

        params = {
            "dbname": config["name"],
            "user": config["user"],
            "password": config["password"],
            "host": config["host"],
            "port": config["port"],
            "connect_timeout": 10,
        }
        try:
            with psycopg.connect(**params):
                return True, "connection established"
        except Exception as exc:
            return False, str(exc)

    def _write_lock(self, config: dict[str, str]) -> None:
        """Persist PostgreSQL settings in ``.locks/postgres.lck``."""

        lock_path = Path(settings.BASE_DIR) / ".locks" / "postgres.lck"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def _upsert_runtime_config(self, config: dict[str, str]) -> None:
        """Create or update active ``DatabaseConfig`` row."""

        DatabaseConfig.objects.update_or_create(
            backend="postgres",
            host=config["host"],
            port=int(config["port"]),
            name=config["name"],
            defaults={
                "user": config["user"],
                "password": config["password"],
                "is_active": True,
            },
        )

    def _record_status(self, config: dict[str, str], *, ok: bool, error: str) -> None:
        """Write latest health-check result to ``DatabaseConfig``."""

        obj = (
            DatabaseConfig.objects.filter(
                backend="postgres",
                host=config["host"],
                port=int(config["port"]),
                name=config["name"],
            )
            .order_by("-updated_at")
            .first()
        )
        if obj is None:
            return
        obj.last_checked_at = timezone.now()
        obj.last_status_ok = ok
        obj.last_error = "" if ok else error[:2000]
        obj.save(update_fields=["last_checked_at", "last_status_ok", "last_error", "updated_at"])

    def _migrate_sqlite_to_postgres(
        self, *, sqlite_path: str, postgres_config: dict[str, str]
    ) -> bool:
        """Migrate current SQLite dataset into PostgreSQL using dumpdata/loaddata."""

        source = Path(sqlite_path) if sqlite_path else Path(settings.BASE_DIR) / "db.sqlite3"
        if not source.exists():
            return False

        dump_path = Path(settings.BASE_DIR) / "work" / "postgres_migration_dump.json"
        dump_path.parent.mkdir(parents=True, exist_ok=True)

        base_env = os.environ.copy()
        sqlite_env = base_env | {
            "ARTHEXIS_DB_BACKEND": "sqlite",
            "ARTHEXIS_SQLITE_PATH": str(source),
        }
        postgres_env = base_env | {
            "ARTHEXIS_DB_BACKEND": "postgres",
            "POSTGRES_HOST": postgres_config["host"],
            "POSTGRES_PORT": str(postgres_config["port"]),
            "POSTGRES_DB": postgres_config["name"],
            "POSTGRES_USER": postgres_config["user"],
            "POSTGRES_PASSWORD": postgres_config["password"],
        }

        dump_cmd = [
            sys.executable,
            "manage.py",
            "dumpdata",
            "--natural-foreign",
            "--natural-primary",
            "--exclude",
            "contenttypes",
            "--exclude",
            "auth.permission",
            f"--output={dump_path}",
            "--verbosity=0",
        ]
        load_cmd = [
            sys.executable,
            "manage.py",
            "migrate",
            "--noinput",
            "--verbosity=0",
        ]
        loaddata_cmd = [
            sys.executable,
            "manage.py",
            "loaddata",
            str(dump_path),
            "--ignorenonexistent",
            "--verbosity=0",
        ]

        for command, env in (
            (dump_cmd, sqlite_env),
            (load_cmd, postgres_env),
            (loaddata_cmd, postgres_env),
        ):
            result = subprocess.run(command, env=env, cwd=str(settings.BASE_DIR), check=False)
            if result.returncode != 0:
                raise CommandError(f"Migration step failed: {' '.join(command)}")

        return True
