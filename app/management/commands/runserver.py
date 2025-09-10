import os
import webbrowser

from django.apps import apps
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.conf import settings
from daphne.management.commands.runserver import (
    Command as RunserverCommand,
    get_default_application,
)


class Command(RunserverCommand):
    """Extended runserver command that also prints WebSocket URLs and admin link."""

    def get_application(self, options):
        """Serve static files even when DEBUG is False."""
        staticfiles_installed = apps.is_installed("django.contrib.staticfiles")
        use_static_handler = options.get("use_static_handler", staticfiles_installed)
        if use_static_handler:
            return ASGIStaticFilesHandler(get_default_application())
        return get_default_application()

    def on_bind(self, server_port):
        super().on_bind(server_port)
        host = self.addr or (
            self.default_addr_ipv6 if self.use_ipv6 else self.default_addr
        )
        scheme = "wss" if self.ssl_options else "ws"
        # Display available websocket URLs.
        websocket_paths = ["/ws/echo/", "/<path>/<cid>/"]
        for path in websocket_paths:
            self.stdout.write(
                f"WebSocket available at {scheme}://{host}:{server_port}{path}"
            )
        http_scheme = "https" if self.ssl_options else "http"
        self.stdout.write(
            f"Admin available at {http_scheme}://{host}:{server_port}/admin/"
        )

        if os.environ.get("DJANGO_DEV_RELOAD") and os.environ.get("RUN_MAIN") == "true":
            webbrowser.open(f"{http_scheme}://{host}:{server_port}/")
