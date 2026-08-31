"""URL routes for explicit supported actions APIs."""

from django.urls import path

from apps.actions.api import views

urlpatterns = [
    path("v1/security-groups/", views.security_groups, name="actions-api-security-groups"),
]
