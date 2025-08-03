#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

from config.loadenv import loadenv


BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
VENV_BIN = "Scripts" if os.name == "nt" else "bin"
VENV_PYTHON = VENV_DIR / VENV_BIN / ("python.exe" if os.name == "nt" else "python")


def _ensure_venv() -> None:
    """Ensure the local virtual environment has all dependencies."""
    try:
        import subprocess

        if not VENV_PYTHON.exists():
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=False)
        if not VENV_PYTHON.exists():
            return

        req = Path("requirements.txt")
        if not req.exists():
            return

        freeze = subprocess.run(
            [str(VENV_PYTHON), "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            check=False,
        )
        installed = {}
        for line in freeze.stdout.splitlines():
            if "==" in line:
                name, ver = line.split("==", 1)
                installed[name.lower()] = ver
            elif "@" in line:
                name = line.split("@", 1)[0]
                installed[name.strip().lower()] = None

        from packaging.requirements import Requirement

        req_lines = [
            line.strip()
            for line in req.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        needs_install = False
        for line in req_lines:
            try:
                requirement = Requirement(line)
            except Exception:
                needs_install = True
                break
            name = requirement.name.lower()
            version = installed.get(name)
            if version is None:
                if name not in installed:
                    needs_install = True
                    break
            elif requirement.specifier and not requirement.specifier.contains(
                version, prereleases=True
            ):
                needs_install = True
                break
        if needs_install:
            subprocess.run(
                [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(req)],
                check=False,
            )
    except Exception as exc:  # pragma: no cover - dev helper
        print(f"Dev task error: {exc}")


def _runserver_tasks() -> None:
    """Perform optional maintenance tasks when launching the dev server."""
    try:
        import subprocess

        import django
        from django.conf import settings
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.db import connection
        from django.db.migrations.exceptions import InconsistentMigrationHistory
        from django.db.migrations.recorder import MigrationRecorder

        django.setup()
        if not settings.DEBUG:
            return

        try:
            call_command("makemigrations", interactive=False)
        except CommandError:
            call_command("makemigrations", merge=True, interactive=False)
        try:
            call_command("migrate", interactive=False)
        except InconsistentMigrationHistory:
            MigrationRecorder(connection).migration_qs.filter(app="ocpp").delete()
            call_command("migrate", interactive=False)

        proc = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True
        )
        if proc.stdout.strip():
            subprocess.run(["git", "add", "-A"], check=False)
            subprocess.run(
                ["git", "commit", "-m", "Auto migrations"],
                check=False,
            )
            subprocess.run(["git", "push"], check=False)
    except Exception as exc:  # pragma: no cover - dev helper
        print(f"Dev task error: {exc}")


def main():
    """Run administrative tasks."""
    loadenv()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
        from django.conf import settings
        from django.utils.autoreload import run_with_reloader
        # Always serve the ASGI application by replacing Django's runserver
        from daphne.management.commands.runserver import (
            Command as DaphneRunserver,
        )
        from django.core.management.commands import runserver as core_runserver

        core_runserver.Command = DaphneRunserver

        # Patch the runserver command to display WebSocket URLs and admin link
        # when it binds
        def patched_on_bind(self, server_port):
            original_on_bind(self, server_port)
            host = self.addr or (
                self.default_addr_ipv6 if self.use_ipv6 else self.default_addr
            )
            scheme = "wss" if getattr(self, "ssl_options", None) else "ws"
            for path in ["/ws/echo/", "/<path>/<cid>/"]:
                self.stdout.write(
                    f"WebSocket available at {scheme}://{host}:{server_port}{path}"
                )
            http_scheme = "https" if getattr(self, "ssl_options", None) else "http"
            self.stdout.write(
                f"Admin available at {http_scheme}://{host}:{server_port}/admin/"
            )

        original_on_bind = core_runserver.Command.on_bind
        core_runserver.Command.on_bind = patched_on_bind
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    def _execute():
        _ensure_venv()
        if (
            settings.DEBUG
            and VENV_PYTHON.exists()
            and Path(sys.executable).resolve() != VENV_PYTHON.resolve()
        ):
            os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])
        if settings.DEBUG and len(sys.argv) > 1 and sys.argv[1] == "runserver":
            _runserver_tasks()
        execute_from_command_line(sys.argv)

    if (
        settings.DEBUG
        and os.environ.get("RUN_MAIN") != "true"
        and len(sys.argv) > 1
        and sys.argv[1] == "runserver"
    ):
        run_with_reloader(_execute)
    else:
        _execute()


if __name__ == "__main__":
    main()
