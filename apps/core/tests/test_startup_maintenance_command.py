from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command


def test_startup_maintenance_command_runs_registered_cleanup_tasks():
    out = StringIO()

    with (
        patch(
            "apps.core.management.commands.startup_maintenance._reset_cached_statuses",
            return_value=5,
        ) as reset,
        patch(
            "apps.core.management.commands.startup_maintenance._purge_view_history",
            return_value=7,
        ) as purge,
        patch(
            "apps.core.management.commands.startup_maintenance._write_agents_context",
            return_value=SimpleNamespace(
                written=True, path=Path("work/codex/AGENTS.md")
            ),
        ) as write_agents,
    ):
        call_command(
            "startup_maintenance",
            "--view-history-days",
            "20",
            stdout=out,
        )

    reset.assert_called_once_with()
    purge.assert_called_once_with(days=20)
    write_agents.assert_called_once_with()
    output = out.getvalue()
    assert "OCPP cached statuses cleared: 5" in output
    assert "Site view history entries purged (older than 20 days): 7" in output
    assert "Local AGENTS context written: work\\codex\\AGENTS.md" in output or (
        "Local AGENTS context written: work/codex/AGENTS.md" in output
    )


def test_startup_maintenance_command_enforces_minimum_retention_days():
    out = StringIO()

    with (
        patch(
            "apps.core.management.commands.startup_maintenance._reset_cached_statuses",
            return_value=0,
        ),
        patch(
            "apps.core.management.commands.startup_maintenance._purge_view_history",
            return_value=2,
        ) as purge,
        patch(
            "apps.core.management.commands.startup_maintenance._write_agents_context",
            return_value=SimpleNamespace(
                written=False, path=Path("work/codex/AGENTS.md")
            ),
        ),
    ):
        call_command("startup_maintenance", "--view-history-days", "0", stdout=out)

    purge.assert_called_once_with(days=1)
    output = out.getvalue()
    assert "Site view history entries purged (older than 1 days): 2" in output


def test_startup_maintenance_command_skips_disabled_app_tasks(settings):
    settings.INSTALLED_APPS = ["django.contrib.sites", "apps.core"]
    out = StringIO()

    with (
        patch(
            "apps.core.management.commands.startup_maintenance._reset_cached_statuses"
        ) as reset,
        patch(
            "apps.core.management.commands.startup_maintenance._purge_view_history"
        ) as purge,
        patch(
            "apps.core.management.commands.startup_maintenance._coerce_view_history_days"
        ) as coerce_days,
        patch(
            "apps.core.management.commands.startup_maintenance._write_agents_context"
        ) as write_agents,
    ):
        call_command("startup_maintenance", stdout=out)

    reset.assert_not_called()
    purge.assert_not_called()
    coerce_days.assert_not_called()
    write_agents.assert_not_called()
    output = out.getvalue()
    assert (
        "OCPP cached statuses skipped: apps.ocpp is not installed for this node profile"
        in output
    )
    assert (
        "Site view history purge skipped: apps.sites is not installed for this node profile"
        in output
    )
    assert (
        "Local AGENTS context skipped: apps.skills is not installed for this node profile"
        in output
    )
