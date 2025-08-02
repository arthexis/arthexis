from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/sink/$", consumers.SinkConsumer.as_asgi()),
    # Accept connections at any path; the last segment is the charger ID
    re_path(r"^(?:.*/)?(?P<cid>[^/]+)/?$", consumers.CSMSConsumer.as_asgi()),
]
