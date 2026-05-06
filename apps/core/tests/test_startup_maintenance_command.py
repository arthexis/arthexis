from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command


def test_startup_maintenance_command_runs_registered_cleanup_tasks():
    out = StringIO()

    with (
        patch(
            "apps.core.management.commands.startup_maintenance.reset_cached_statuses",
            return_value=5,
        ) as reset,
        patch(
            "apps.core.management.commands.startup_maintenance.purge_view_history",
            return_value=7,
        ) as purge,
        patch(
            "apps.core.management.commands.startup_maintenance.write_agents_context",
            return_value=SimpleNamespace(
                written=True, path=Path("work/codex/AGENTS.md")
            ),
        ) as write_agents,
    ):
        call_command("startup_maintenance", "--view-history-days", "20", stdout=out)

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
            "apps.core.management.commands.startup_maintenance.reset_cached_statuses",
            return_value=0,
        ),
        patch(
            "apps.core.management.commands.startup_maintenance.purge_view_history",
            return_value=2,
        ) as purge,
        patch(
            "apps.core.management.commands.startup_maintenance.write_agents_context",
            return_value=SimpleNamespace(
                written=False, path=Path("work/codex/AGENTS.md")
            ),
        ),
    ):
        call_command("startup_maintenance", "--view-history-days", "0", stdout=out)

    purge.assert_called_once_with(days=1)
    assert "Site view history entries purged (older than 1 days): 2" in out.getvalue()
