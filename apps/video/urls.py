from django.urls import path

from . import views

app_name = "video"

urlpatterns = [
    path("<slug:slug>/", views.stream_detail, name="stream-detail"),
    path("<slug:slug>/mjpeg/", views.mjpeg_stream, name="mjpeg-stream"),
]
