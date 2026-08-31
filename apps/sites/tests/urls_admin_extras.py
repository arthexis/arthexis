"""Supplemental URL patterns for admin extras tests."""

from django.http import HttpResponse
from django.urls import path

from config.urls import urlpatterns as project_urlpatterns


def _ok_view(request, *args, **kwargs):
    """Return a basic success response for reverseable test endpoints."""

    del request, args, kwargs
    return HttpResponse("ok")


urlpatterns = list(project_urlpatterns) + [
    path("test/dashboard-action/", _ok_view, name="test_dashboard_action"),
    path("test/tools/<str:tool>/", _ok_view, name="test_admin_action_tool"),
]
