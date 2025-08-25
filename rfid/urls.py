from django.urls import path
from . import views

urlpatterns = [
    path("", views.reader, name="rfid-reader"),
    path("scan/next/", views.scan_next, name="rfid-scan-next"),
]
