from __future__ import annotations

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

OLD_TARGETS = [
    ("analytics", None),
    ("core", "0003_release_email_models"),
    ("ops", "0002_initial"),
    ("pages", "0004_remove_workgroup_play_panel_item"),
    ("release", "0002_alter_package_test_command"),
]

NEW_TARGETS = [
    ("analytics", "0001_adopt_core_usage_event"),
    ("core", "0004_release_remaining_models"),
    ("ops", "0003_adopt_core_admin_notice"),
    ("pages", "0005_adopt_core_invite_lead"),
    ("release", "0003_move_upgrade_permission"),
]

UPGRADE_PERMISSION_CODENAME = "can_trigger_upgrade_checks"


def _apps_for(targets):
    executor = MigrationExecutor(connection)
    state_targets = [target for target in targets if target[1] is not None]
    return executor, executor.loader.project_state(state_targets).apps


def _assert_forward_state(apps, expected):
    UsageEvent = apps.get_model("analytics", "UsageEvent")
    InviteLead = apps.get_model("pages", "InviteLead")
    AdminNotice = apps.get_model("ops", "AdminNotice")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")

    usage = UsageEvent.objects.get(pk=expected["usage_pk"])
    invite = InviteLead.objects.get(pk=expected["invite_pk"])
    notice = AdminNotice.objects.get(pk=expected["notice_pk"])

    assert usage.path == "/migration-regression"
    assert usage.metadata == {"source": "migration-regression"}
    assert invite.email == "migration-regression@example.com"
    assert notice.message == "Migration regression notice"

    owner_labels = {
        "usageevent": "analytics",
        "invitelead": "pages",
        "adminnotice": "ops",
    }
    for model_name, app_label in owner_labels.items():
        content_type = ContentType.objects.get(app_label=app_label, model=model_name)
        assert content_type.pk == expected["content_type_pks"][model_name]
        assert not ContentType.objects.filter(
            app_label="core",
            model=model_name,
        ).exists()

    permission = Permission.objects.get(
        content_type__app_label="release",
        content_type__model="releasepermission",
        codename=UPGRADE_PERMISSION_CODENAME,
    )
    assert permission.pk == expected["permission_pk"]

    group = Group.objects.get(pk=expected["group_pk"])
    assert group.permissions.filter(pk=expected["permission_pk"]).exists()

    log_entry = LogEntry.objects.get(pk=expected["log_entry_pk"])
    assert log_entry.content_type_id == expected["content_type_pks"]["adminnotice"]
    assert log_entry.object_id == str(expected["notice_pk"])


def _assert_rollback_state(apps, expected):
    UsageEvent = apps.get_model("core", "UsageEvent")
    InviteLead = apps.get_model("core", "InviteLead")
    AdminNotice = apps.get_model("core", "AdminNotice")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")

    assert UsageEvent.objects.filter(pk=expected["usage_pk"]).exists()
    assert InviteLead.objects.filter(pk=expected["invite_pk"]).exists()
    assert AdminNotice.objects.filter(pk=expected["notice_pk"]).exists()

    for model_name, content_type_pk in expected["content_type_pks"].items():
        content_type = ContentType.objects.get(app_label="core", model=model_name)
        assert content_type.pk == content_type_pk

    permission = Permission.objects.get(
        content_type__app_label="core",
        content_type__model="adminnotice",
        codename=UPGRADE_PERMISSION_CODENAME,
    )
    assert permission.pk == expected["permission_pk"]

    group = Group.objects.get(pk=expected["group_pk"])
    assert group.permissions.filter(pk=expected["permission_pk"]).exists()

    log_entry = LogEntry.objects.get(pk=expected["log_entry_pk"])
    assert log_entry.content_type_id == expected["content_type_pks"]["adminnotice"]
    assert log_entry.object_id == str(expected["notice_pk"])


@pytest.mark.django_db(transaction=True)
def test_remaining_core_model_ownership_upgrade_and_rollback_preserve_identity():
    """Exercise Step 7 as an upgrade of populated data, not only a fresh install."""

    initial_executor = MigrationExecutor(connection)
    if not initial_executor.loader.graph.nodes:
        pytest.skip(
            "Migration graph is disabled; run this regression in a fresh process "
            "with PYTEST_DISABLE_MIGRATIONS=0."
        )
    latest_targets = initial_executor.loader.graph.leaf_nodes()

    User = get_user_model()
    user = User.all_objects.create_user(
        username="migration-boundary-user",
        email="migration-boundary-user@example.com",
        password="migration-boundary-test-password",
    )

    try:
        initial_executor.migrate(OLD_TARGETS)
        _, old_apps = _apps_for(OLD_TARGETS)

        UsageEvent = old_apps.get_model("core", "UsageEvent")
        InviteLead = old_apps.get_model("core", "InviteLead")
        AdminNotice = old_apps.get_model("core", "AdminNotice")
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        Permission = old_apps.get_model("auth", "Permission")
        Group = old_apps.get_model("auth", "Group")

        usage = UsageEvent.objects.create(
            app_label="core",
            view_name="migration-regression",
            path="/migration-regression",
            method="GET",
            status_code=200,
            model_label="core.AdminNotice",
            action="read",
            metadata={"source": "migration-regression"},
        )
        invite = InviteLead.objects.create(
            email="migration-regression@example.com",
            path="/invite/migration-regression",
        )
        notice = AdminNotice.objects.create(message="Migration regression notice")

        content_type_pks = {
            model_name: ContentType.objects.get(
                app_label="core",
                model=model_name,
            ).pk
            for model_name in ("usageevent", "invitelead", "adminnotice")
        }

        permission = Permission.objects.get(
            content_type__app_label="core",
            content_type__model="adminnotice",
            codename=UPGRADE_PERMISSION_CODENAME,
        )
        group = Group.objects.create(name="migration-boundary-upgrade-checkers")
        Group.permissions.through.objects.create(
            group_id=group.pk,
            permission_id=permission.pk,
        )

        # django.contrib.admin is intentionally outside OLD_TARGETS/NEW_TARGETS.
        # Use its unchanged runtime model as an external FK-reference fixture while
        # keeping all models whose migration state changes on the historical apps.
        log_entry = LogEntry.objects.create(
            action_flag=1,
            change_message="migration regression",
            content_type_id=content_type_pks["adminnotice"],
            object_id=str(notice.pk),
            object_repr="Migration regression notice",
            user_id=user.pk,
        )

        expected = {
            "usage_pk": usage.pk,
            "invite_pk": invite.pk,
            "notice_pk": notice.pk,
            "content_type_pks": content_type_pks,
            "permission_pk": permission.pk,
            "group_pk": group.pk,
            "log_entry_pk": log_entry.pk,
        }

        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(NEW_TARGETS)
        _, new_apps = _apps_for(NEW_TARGETS)
        _assert_forward_state(new_apps, expected)

        rollback_executor = MigrationExecutor(connection)
        rollback_executor.migrate(OLD_TARGETS)
        _, rolled_back_apps = _apps_for(OLD_TARGETS)
        _assert_rollback_state(rolled_back_apps, expected)
    finally:
        restore_executor = MigrationExecutor(connection)
        restore_executor.migrate(latest_targets)
