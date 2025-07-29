#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
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
        execute_from_command_line(sys.argv)

    if (
        os.environ.get("DJANGO_DEV_RELOAD")
        and settings.DEBUG
        and os.environ.get("RUN_MAIN") != "true"
    ):
        run_with_reloader(_execute)
    else:
        _execute()


if __name__ == "__main__":
    main()
