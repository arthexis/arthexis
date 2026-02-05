from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/video/(?P<slug>[-\w]+)/$", consumers.RedisStreamConsumer.as_asgi()),
    re_path(
        r"^ws/video/(?P<slug>[-\w]+)/webrtc/$",
        consumers.WebRTCSignalingConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/video/(?P<slug>[-\w]+)/admin/$",
        consumers.RedisStreamConsumer.as_asgi(),
        {"admin": True},
    ),
    re_path(
        r"^ws/video/(?P<slug>[-\w]+)/admin/webrtc/$",
        consumers.WebRTCSignalingConsumer.as_asgi(),
        {"admin": True},
    ),
]

__all__ = ["websocket_urlpatterns"]
