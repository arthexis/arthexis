from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/pages/chat/$", consumers.ChatConsumer.as_asgi()),
]


__all__ = ["websocket_urlpatterns"]
