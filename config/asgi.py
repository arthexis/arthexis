"""ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from config.loadenv import loadenv
from config.sqlite_driver import bootstrap_sqlite_driver

loadenv()
bootstrap_sqlite_driver()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.apps import apps as django_apps
from django.core.asgi import get_asgi_application

from apps.core.checks.apps_registry import enforce_apps_registry_configuration
from config.route_providers import autodiscovered_websocket_urlpatterns

django_asgi_app = get_asgi_application()
enforce_apps_registry_configuration()

websocket_patterns = autodiscovered_websocket_urlpatterns()

if django_apps.is_installed("apps.nodes"):
    from apps.nodes.services.sibling_ipc import start_server as start_sibling_ipc_server

    start_sibling_ipc_server()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_patterns)),
    }
)
