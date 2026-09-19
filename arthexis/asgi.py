"""ASGI configuration for Arthexis."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")

django_asgi_application = get_asgi_application()


def build_application():
    """Build HTTP and WebSocket routing after Django initializes its app registry."""
    from channels.auth import AuthMiddlewareStack
    from channels.routing import ProtocolTypeRouter, URLRouter

    from apps.ocpp.routes import websocket_urlpatterns

    return ProtocolTypeRouter(
        {
            "http": django_asgi_application,
            "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
        }
    )


application = build_application()
