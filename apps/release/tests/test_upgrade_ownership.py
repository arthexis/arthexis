from __future__ import annotations

import importlib

from celery import current_app
from django.core.management import get_commands


def test_release_owns_upgrade_modules_with_core_compatibility_aliases():
    release_versioning = importlib.import_module("apps.release.versioning")
    release_auto_upgrade = importlib.import_module("apps.release.auto_upgrade")
    release_changelog = importlib.import_module("apps.release.changelog")
    release_upgrade = importlib.import_module("apps.release.upgrade")

    assert importlib.import_module("apps.core.versioning") is release_versioning
    assert importlib.import_module("apps.core.auto_upgrade") is release_auto_upgrade
    assert importlib.import_module("apps.core.changelog") is release_changelog
    assert importlib.import_module("apps.core.system.upgrade") is release_upgrade


def test_release_owns_auto_upgrade_task_modules():
    for module_name in ("locks", "network", "runner", "scheduling", "tasks"):
        release_module = importlib.import_module(
            f"apps.release.tasks.auto_upgrade.{module_name}"
        )
        legacy_module = importlib.import_module(
            f"apps.core.tasks.auto_upgrade.{module_name}"
        )
        assert legacy_module is release_module


def test_auto_upgrade_celery_task_names_keep_transition_aliases():
    release_tasks = importlib.import_module("apps.release.tasks.auto_upgrade.tasks")

    assert release_tasks.check_github_updates.name == (
        "apps.release.tasks.auto_upgrade.tasks.check_github_updates"
    )
    assert release_tasks.verify_auto_upgrade_health.name == (
        "apps.release.tasks.auto_upgrade.tasks.verify_auto_upgrade_health"
    )
    assert "apps.core.tasks.auto_upgrade.tasks.check_github_updates" in current_app.tasks
    assert (
        "apps.core.tasks.auto_upgrade.tasks.verify_auto_upgrade_health"
        in current_app.tasks
    )


def test_changelog_command_implementation_is_release_owned():
    legacy_command = importlib.import_module(
        "apps.core.management.commands.changelog"
    ).Command
    release_command = importlib.import_module(
        "apps.release.management.commands.changelog"
    ).Command

    assert legacy_command is release_command
    assert release_command.__module__ == "apps.release.management.commands.changelog"
    assert get_commands()["changelog"] in {"apps.core", "apps.release"}
