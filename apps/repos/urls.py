from django.urls import path

from apps.repos.views import dashboard, webhooks

app_name = "repos"

urlpatterns = [
    path("work/", dashboard.repository_work_dashboard, name="repository-work-dashboard"),
    path(
        "work/assignments/snapshot/",
        dashboard.repository_work_assignment_snapshot,
        name="repository-work-assignment-snapshot",
    ),
    path(
        "work/assignments/sync/",
        dashboard.repository_work_assignment_sync,
        name="repository-work-assignment-sync",
    ),
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
