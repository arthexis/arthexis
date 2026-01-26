from django.urls import re_path

from .consumers import CSMSConsumer, SinkConsumer

websocket_urlpatterns = [
    re_path(r"^ws/sink/$", SinkConsumer.as_asgi()),
    # Accept connections at any path; the last segment is the charger ID.
    # Some charge points omit the final segment and only provide the
    # identifier via query parameters, so allow an empty match here.
    re_path(r"^(?:.*/)?(?P<cid>[^/]*)/?$", CSMSConsumer.as_asgi()),
]
