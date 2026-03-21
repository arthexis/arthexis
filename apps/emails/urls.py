"""URL routes for user-facing inbox pages."""

from django.urls import path

from . import views

app_name = "emails"

urlpatterns = [
    path("inbox/", views.inbox_list, name="inbox-list"),
    path("inbox/message/<str:message_key>/", views.inbox_detail, name="inbox-detail"),
]
