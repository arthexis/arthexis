"""Helpers for resolving and persisting per-user staff dashboard tasks."""

from __future__ import annotations

from django.urls import NoReverseMatch, reverse

from apps.actions.models import StaffTask, StaffTaskPreference


DEFAULT_STAFF_TASKS: tuple[dict[str, object], ...] = (
    {
        "slug": "actions",
        "label": "Actions",
        "description": "Open personal action OpenAPI and remote action tooling.",
        "admin_url_name": "admin:actions_remoteaction_my_openapi_spec",
        "order": 10,
    },
    {
        "slug": "config",
        "label": "Config",
        "description": "Open configuration shortcuts.",
        "admin_url_name": "admin:config",
        "order": 20,
    },
    {
        "slug": "data",
        "label": "Data",
        "description": "Manage personal admin data and preferences.",
        "admin_url_name": "admin:user_data",
        "order": 30,
    },
    {
        "slug": "discover",
        "label": "Discover",
        "description": "Run node and integration discovery tools.",
        "admin_url_name": "admin:nodes_nodefeature_discover",
        "order": 40,
    },
    {
        "slug": "environment",
        "label": "Environment",
        "description": "Inspect deployment environment details.",
        "admin_url_name": "admin:environment",
        "order": 50,
    },
    {
        "slug": "logs",
        "label": "Logs",
        "description": "Browse system and application logs.",
        "admin_url_name": "admin:log_viewer",
        "order": 60,
    },
    {
        "slug": "rules",
        "label": "Rules",
        "description": "Review dashboard rule evaluation outcomes.",
        "admin_url_name": "admin:system-dashboard-rules-report",
        "order": 70,
    },
    {
        "slug": "reports",
        "label": "Reports",
        "description": "Run system reports and provide query parameters.",
        "admin_url_name": "admin:system-reports",
        "order": 75,
    },
    {
        "slug": "seed",
        "label": "Seed",
        "description": "Load baseline data into the system.",
        "admin_url_name": "admin:seed_data",
        "order": 80,
    },
    {
        "slug": "sigil",
        "label": "Sigil",
        "description": "Build and inspect sigils.",
        "admin_url_name": "admin:sigil_builder",
        "order": 90,
    },
    {
        "slug": "tasks",
        "label": "Tasks",
        "description": "Open the task panels overview and toggles.",
        "admin_url_name": "admin:system",
        "order": 100,
    },
    {
        "slug": "system",
        "label": "System",
        "description": "Inspect system details and service controls.",
        "admin_url_name": "admin:system-details",
        "order": 105,
    },
    {
        "slug": "upgrade",
        "label": "Upgrade",
        "description": "View upgrade status and run upgrade checks.",
        "admin_url_name": "admin:system-upgrade-report",
        "order": 110,
        "superuser_only": True,
    },
)


def visible_staff_tasks_for_user(user) -> list[dict[str, str]]:
    """Return resolved dashboard button payloads for the given user."""

    if not getattr(user, "is_staff", False):
        return []

    ensure_default_staff_tasks_exist()
    tasks = list(StaffTask.objects.filter(is_active=True).order_by("order", "label"))
    if not tasks:
        return []

    pref_map = {
        pref.task_id: pref.is_enabled
        for pref in StaffTaskPreference.objects.filter(user=user, task__in=tasks)
    }

    visible: list[dict[str, str]] = []
    for task in tasks:
        if task.staff_only and not user.is_staff:
            continue
        if task.superuser_only and not user.is_superuser:
            continue
        enabled = pref_map.get(task.pk, task.default_enabled)
        if not enabled:
            continue
        try:
            url = reverse(task.admin_url_name)
        except NoReverseMatch:
            continue
        visible.append({"slug": task.slug, "label": task.label, "url": url})
    return visible


def ensure_default_staff_tasks_exist() -> None:
    """Backfill missing default staff tasks in existing and new environments."""

    for task in DEFAULT_STAFF_TASKS:
        StaffTask.objects.get_or_create(
            slug=task["slug"],
            defaults={
                "label": task["label"],
                "description": task["description"],
                "admin_url_name": task["admin_url_name"],
                "order": task["order"],
                "default_enabled": True,
                "staff_only": True,
                "superuser_only": bool(task.get("superuser_only", False)),
                "is_active": True,
            },
        )
