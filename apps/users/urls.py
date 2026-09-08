from django.urls import path

from .admin_views import stop_impersonation

urlpatterns = [
    path("stop/", stop_impersonation, name="stop-impersonation"),
]
