"""URL routes for assistant profile endpoints."""

from django.urls import path

from . import workgroup_views as views

app_name = "workgroup"

urlpatterns = [
    path(
        "assistant-profiles/<int:user_id>/",
        views.issue_key,
        name="assistantprofile-issue",
    ),
    path("assistant/test/", views.assistant_test, name="assistant-test"),
    path("chat/", views.chat, name="chat"),
]
