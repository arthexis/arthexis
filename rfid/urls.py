from django.urls import path
from . import views

urlpatterns = [
    path("", views.reader, name="rfid-reader"),
    path("<int:label_id>/", views.label, name="rfid-page"),
    path("scan/next/", views.scan_next, name="rfid-scan-next"),
    path("scan/restart/", views.scan_restart, name="rfid-scan-restart"),
    path("scan/test/", views.scan_test, name="rfid-scan-test"),
]
