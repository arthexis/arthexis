from django.urls import path

from . import views

app_name = "video"

urlpatterns = [
    path("cameras/", views.camera_gallery, name="camera-gallery"),
    path("<slug:slug>/", views.stream_detail, name="stream-detail"),
    path("<slug:slug>/mjpeg/", views.mjpeg_stream, name="mjpeg-stream"),
    path("<slug:slug>/mjpeg/probe/", views.mjpeg_probe, name="mjpeg-probe"),
]
