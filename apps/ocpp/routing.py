from django.urls import re_path

from .consumers import CSMSConsumer, SinkConsumer

websocket_urlpatterns = [
    re_path(r"^ws/sink/$", SinkConsumer.as_asgi()),
    re_path(
        r"^(?:[^/]+/)*(?:ocpp|ws/ocpp)(?:/(?P<cid>[^/]+))?/?$",
        CSMSConsumer.as_asgi(),
    ),
    # Legacy compatibility: older onboarding advertised root and /ws/<charger_id>
    # websocket paths, with optional base prefixes.
    # Keep those single-segment paths, but do not let OCPP consume other apps'
    # /ws/<namespace>/... routes.
    re_path(r"^(?:[^/]+/)*ws/(?P<cid>(?!ocpp/?$)[^/]+)/?$", CSMSConsumer.as_asgi()),
    re_path(
        r"^(?!(?:[^/]+/)*(?:ocpp|ws)/)(?:[^/]+/)*(?P<cid>(?!(?:ocpp|ws)/?$)[^/]+)/?$",
        CSMSConsumer.as_asgi(),
    ),
    re_path(r"^$", CSMSConsumer.as_asgi()),
]
