from io import StringIO
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
    ):
        call_command("startup_maintenance", "--view-history-days", "20", stdout=out)

    reset.assert_called_once_with()
    purge.assert_called_once_with(days=20)
    output = out.getvalue()
    assert "OCPP cached statuses cleared: 5" in output
    assert "Site view history entries purged (older than 20 days): 7" in output
