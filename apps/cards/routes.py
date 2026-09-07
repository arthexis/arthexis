"""Root route provider for app-owned URL mounts."""

from django.urls import include, path

from .auth_views import rfid_login
from .core_views import rfid_batch

ROOT_URLPATTERNS = [
    path("cards/", include("apps.cards.urls")),
    # Legacy paths retained while ownership moves out of apps.core.
    path("core/rfid-login/", rfid_login, name="rfid-login"),
    path("core/rfids/", rfid_batch, name="rfid-batch"),
]
