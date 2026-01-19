"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
from config.loadenv import loadenv
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application


def create_asgi_application():
    loadenv()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    django_asgi_app = get_asgi_application()

    # Import routing modules after Django has initialized to ensure models are
    # registered before consumers are loaded.
    import apps.ocpp.routing
    import apps.nodes.routing
    import apps.sites.routing

    websocket_patterns = [
        *apps.sites.routing.websocket_urlpatterns,
        *apps.nodes.routing.websocket_urlpatterns,
        *apps.ocpp.routing.websocket_urlpatterns,
    ]

    return ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": AuthMiddlewareStack(URLRouter(websocket_patterns)),
        }
    )


_application = None


def _get_application():
    global _application
    if _application is None:
        _application = create_asgi_application()
    return _application


def __getattr__(name):
    if name == "application":
        return _get_application()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
