"""WebSocket routes for the retained OCPP transport boundary."""

from django.urls import re_path

from apps.ocpp.consumers import CSMSConsumer

websocket_urlpatterns = [
    re_path(r"ws/ocpp/(?P<charger_identity>[^/]+)/$", CSMSConsumer.as_asgi()),
]
