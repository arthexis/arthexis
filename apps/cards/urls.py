from django.urls import path
from . import views

urlpatterns = [
    path("", views.reader, name="rfid-reader"),
    path(
        "rfid/card/<str:public_token>/",
        views.public_card_usage,
        name="rfid-public-card",
    ),
    path(
        "command-templates/burn/",
        views.command_template_burn,
        name="rfid-command-template-burn",
    ),
    path(
        "command-templates/<slug:slug>/",
        views.command_template_detail,
        name="rfid-command-template-detail",
    ),
    path("scan/next/", views.scan_next, name="rfid-scan-next"),
    path("scan/deep/", views.scan_deep, name="rfid-scan-deep"),
    path("export/", views.export_rfids, name="rfid-export"),
    path("import/", views.import_rfids, name="rfid-import"),
]
