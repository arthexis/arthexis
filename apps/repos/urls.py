from django.urls import path

from apps.repos.views import webhooks

app_name = "repos"

urlpatterns = [
    path("webhooks/github/", webhooks.github_webhook, name="github-webhook"),
    path(
        "webhooks/github/app/<slug:app_slug>/",
        webhooks.github_webhook,
        name="github-webhook-app",
    ),
    path(
        "webhooks/github/<str:owner>/<str:name>/",
        webhooks.github_webhook,
        name="github-webhook-repo",
    ),
]
