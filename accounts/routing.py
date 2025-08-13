from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/rfid/", consumers.RFIDConsumer.as_asgi()),
]
