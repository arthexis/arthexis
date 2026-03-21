"""URL routes for user-facing inbox pages."""

from django.urls import path

from . import views

app_name = "emails"

urlpatterns = [
    path("inbox/", views.inbox_list, name="inbox-list"),
    path("inbox/<int:message_index>/", views.inbox_detail, name="inbox-detail"),
]
