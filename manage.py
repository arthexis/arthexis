#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import subprocess
import sys
from pathlib import Path

from config.loadenv import loadenv


def _notify_and_exit(message: str) -> None:
    """Display a Windows notification, open latest log, and exit."""
    if os.name == "nt":  # pragma: no cover - Windows only behaviour
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "ArtHexis", 0)
        except Exception:
            pass
        logs_dir = Path(__file__).resolve().parent / "logs"
        log_files = list(logs_dir.glob("*.log"))
        if log_files:
            latest = max(log_files, key=lambda p: p.stat().st_mtime)
            try:
                subprocess.Popen(["notepad.exe", str(latest)])
            except Exception:
                pass
    raise SystemExit(message)


def _maybe_sync_git() -> None:
    """Check for pending git updates and sync if possible."""
    if os.environ.get("RUN_MAIN") != "true":
        return
    try:
        from django.conf import settings

        if not settings.DEBUG:
            return
    except Exception:
        return

    subprocess.run(["git", "fetch"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "-uno"], capture_output=True, text=True).stdout
    if "behind" in status:
        if not dirty:
            result = subprocess.run(["git", "pull"], capture_output=True, text=True)
            if result.returncode != 0:
                _notify_and_exit("Git pull failed. Check logs for details.")
        else:
            _notify_and_exit("Uncommitted changes prevent auto-sync.")

def main() -> None:
    """Run administrative tasks."""
    loadenv()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    _maybe_sync_git()
    try:
        from django.core.management import execute_from_command_line
        from daphne.management.commands.runserver import (
            Command as DaphneRunserver,
        )
        from django.core.management.commands import runserver as core_runserver

        core_runserver.Command = DaphneRunserver

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
    except ImportError as exc:  # pragma: no cover - Django bootstrap
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    execute_from_command_line(sys.argv)

if __name__ == "__main__":  # pragma: no cover - script entry
    main()
