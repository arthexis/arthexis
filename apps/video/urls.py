from django.urls import path

from . import views

app_name = "video"

urlpatterns = [
    path("cameras/", views.camera_gallery, name="camera-gallery"),
    path("<slug:slug>/", views.stream_detail, name="stream-detail"),
    path("<slug:slug>/mjpeg/admin/", views.mjpeg_admin_stream, name="mjpeg-admin-stream"),
    path("<slug:slug>/mjpeg/admin/probe/", views.mjpeg_admin_probe, name="mjpeg-admin-probe"),
    path("<slug:slug>/mjpeg/debug/", views.mjpeg_debug, name="mjpeg-debug"),
    path(
        "<slug:slug>/mjpeg/debug/status/",
        views.mjpeg_debug_status,
        name="mjpeg-debug-status",
    ),
    path("<slug:slug>/mjpeg/", views.mjpeg_stream, name="mjpeg-stream"),
    path("<slug:slug>/mjpeg/probe/", views.mjpeg_probe, name="mjpeg-probe"),
]
