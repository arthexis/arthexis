"""URL routes for chat profile endpoints."""

from django.urls import path

from . import workgroup_views as views

app_name = "workgroup"

urlpatterns = [
    path("chat-profiles/<int:user_id>/", views.issue_key, name="chatprofile-issue"),
    path("assistant/test/", views.assistant_test, name="assistant-test"),
]
