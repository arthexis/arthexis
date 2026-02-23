"""URL routes for remote actions API."""

from django.urls import path

from apps.actions.api import views

urlpatterns = [
    path("v1/security-groups/", views.security_groups, name="actions-api-security-groups"),
    path("v1/remote/<slug:slug>/", views.invoke_action, name="actions-api-invoke"),
]
