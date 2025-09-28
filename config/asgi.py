"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
from config.loadenv import loadenv
from typing import Any, Awaitable, Callable, Dict, MutableMapping
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
import ocpp.routing

from core.mcp.asgi import application as mcp_application
from core.mcp.asgi import is_mcp_scope

loadenv()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]

websocket_patterns = ocpp.routing.websocket_urlpatterns

async def http_application(scope: Scope, receive: Receive, send: Send) -> None:
    if is_mcp_scope(scope):
        await mcp_application(scope, receive, send)
    else:
        await django_asgi_app(scope, receive, send)

application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_patterns)),
    }
)
