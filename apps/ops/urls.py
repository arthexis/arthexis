"""URL routes for the operations app."""

from django.urls import path

from . import views

app_name = "ops"

urlpatterns = [
    path("clear-active/", views.clear_active_operation, name="clear-active"),
    path(
        "operator-journey/steps/<int:step_id>/complete/",
        views.complete_operator_journey_step_legacy,
        name="operator-journey-step-complete-legacy",
    ),
    path(
        "operator-journey/steps/<int:step_id>/",
        views.operator_journey_step_legacy,
        name="operator-journey-step-legacy",
    ),
    path(
        "operator-journey/steps/<slug:journey_slug>/<slug:step_slug>/",
        views.operator_journey_step,
        name="operator-journey-step",
    ),
    path(
        "operator-journey/steps/<slug:journey_slug>/<slug:step_slug>/complete/",
        views.complete_operator_journey_step,
        name="operator-journey-step-complete",
    ),
    path(
        "operator-journey/steps/<slug:journey_slug>/<slug:step_slug>/github/login/",
        views.operator_journey_github_login,
        name="operator-journey-github-login",
    ),
    path(
        "operator-journey/steps/<slug:journey_slug>/<slug:step_slug>/github/callback/",
        views.operator_journey_github_callback,
        name="operator-journey-github-callback",
    ),
    path("status/surface/", views.status_surface, name="status-surface"),
    path("status/logs/", views.status_log_excerpts, name="status-logs"),
]
