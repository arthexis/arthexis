#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

from config.loadenv import loadenv


def main() -> None:
    """Run administrative tasks."""
    loadenv()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
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
