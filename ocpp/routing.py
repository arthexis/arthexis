from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/ocpp/(?P<cid>[^/]+)/$", consumers.CSMSConsumer.as_asgi()),
]
