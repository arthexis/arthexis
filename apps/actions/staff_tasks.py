"""Helpers for resolving and persisting per-user staff dashboard tasks."""

from __future__ import annotations

from apps.actions.models import StaffTask, StaffTaskPreference


UPGRADE_CHECK_PERMISSION = "core.can_trigger_upgrade_checks"


DEFAULT_STAFF_TASKS: tuple[dict[str, object], ...] = (
    {
        "slug": "config",
        "label": "Config",
        "description": "Open configuration shortcuts.",
        "action_name": "config",
        "order": 20,
    },
    {
        "slug": "data",
        "label": "Data",
        "description": "Manage personal admin data and preferences.",
        "action_name": "data",
        "order": 30,
    },
    {
        "slug": "discover",
        "label": "Discover",
        "description": "Run node and integration discovery tools.",
        "action_name": "discover",
        "order": 40,
    },
    {
        "slug": "environment",
        "label": "Environment",
        "description": "Inspect deployment environment details.",
        "action_name": "environment",
        "order": 50,
    },
    {
        "slug": "evergo",
        "label": "Evergo",
        "description": "Open the Evergo contractor setup or order load workspace.",
        "action_name": "evergo",
        "order": 52,
    },
    {
        "slug": "groups",
        "label": "Groups",
        "description": "Browse the current user's security groups.",
        "action_name": "groups",
        "order": 55,
    },
    {
        "slug": "imager",
        "label": "Imager",
        "description": "Open the Raspberry Pi image builder wizard.",
        "action_name": "imager",
        "order": 58,
    },
    {
        "slug": "logs",
        "label": "Logs",
        "description": "Browse system and application logs.",
        "action_name": "logs",
        "order": 60,
    },
    {
        "slug": "rules",
        "label": "Rules",
        "description": "Review dashboard rule evaluation outcomes.",
        "action_name": "rules",
        "order": 70,
    },
    {
        "slug": "reports",
        "label": "Reports",
        "description": "Run system reports and provide query parameters.",
        "action_name": "reports",
        "order": 75,
    },
    {
        "slug": "seed",
        "label": "Seed",
        "description": "Load baseline data into the system.",
        "action_name": "seed",
        "order": 80,
    },
    {
        "slug": "sigil",
        "label": "Sigil",
        "description": "Build and inspect sigils.",
        "action_name": "sigil",
        "order": 90,
    },
    {
        "slug": "tasks",
        "label": "Tasks",
        "description": "Open the task panels overview and toggles.",
        "action_name": "tasks",
        "order": 100,
    },
    {
        "slug": "system",
        "label": "System",
        "description": "Inspect system details and service controls.",
        "action_name": "system",
        "order": 105,
    },
    {
        "slug": "upgrade",
        "label": "Upgrade",
        "description": "View upgrade status and run upgrade checks.",
        "action_name": "upgrade",
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
        if not user_can_access_staff_task(user, task):
            continue
        enabled = pref_map.get(task.pk, task.default_enabled)
        if not enabled:
            continue
        url = task.resolve_url()
        if not url:
            continue
        visible.append({"slug": task.slug, "label": task.label, "url": url})
    return visible


def can_trigger_upgrade_checks(user) -> bool:
    """Return whether the user may trigger upgrade checks."""

    if not getattr(user, "is_authenticated", False):
        return False

    return bool(user.is_superuser or user.has_perm(UPGRADE_CHECK_PERMISSION))


def user_can_access_staff_task(user, task: StaffTask) -> bool:
    """Return whether the given user can access the task panel action."""

    if task.staff_only and not user.is_staff:
        return False
    if task.action_name == "upgrade":
        return can_trigger_upgrade_checks(user)
    if task.superuser_only and not user.is_superuser:
        return False
    return True



def ensure_default_staff_tasks_exist() -> None:
    """Backfill missing default staff tasks in existing and new environments.

    Existing rows are left intact so operator edits to labels, ordering, and
    visibility remain persistent after the defaults have been seeded once.
    """

    for task in DEFAULT_STAFF_TASKS:
        StaffTask.objects.get_or_create(
            slug=task["slug"],
            defaults={
                "label": task["label"],
                "description": task["description"],
                "action_name": task["action_name"],
                "order": task["order"],
                "default_enabled": True,
                "staff_only": True,
                "superuser_only": bool(task.get("superuser_only", False)),
                "is_active": True,
            },
        )
