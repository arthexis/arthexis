from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError
from django.test import SimpleTestCase

from apps.sites import maintenance
from apps.sites.tasks import purge_view_history_task


class ViewHistoryMaintenanceTests(SimpleTestCase):
    def test_purge_view_history_enforces_minimum_days(self):
        with patch("apps.sites.maintenance.ViewHistory") as view_history:
            view_history.purge_older_than.return_value = 1
            deleted = maintenance.purge_view_history(days=0)

        assert deleted == 1
        view_history.purge_older_than.assert_called_once_with(days=1)

    def test_purge_view_history_returns_zero_when_database_unavailable(self):
        with patch("apps.sites.maintenance.ViewHistory") as view_history:
            view_history.purge_older_than.side_effect = DatabaseError
            with patch.object(maintenance, "logger") as logger:
                deleted = maintenance.purge_view_history(days=10)

        assert deleted == 0
        logger.debug.assert_called_once()

    def test_purge_view_history_task_delegates_to_maintenance(self):
        with patch("apps.sites.tasks.purge_view_history", return_value=4) as purge:
            deleted = purge_view_history_task(days=12)

        assert deleted == 4
        purge.assert_called_once_with(days=12)

    def test_purge_view_history_command_enforces_minimum_days(self):
        out = StringIO()
        with patch("apps.sites.management.commands.purge_view_history.purge_view_history", return_value=2) as purge:
            call_command("purge_view_history", "--days", "0", stdout=out)

        purge.assert_called_once_with(days=1)
        assert "Purged 2 view history entries older than 1 days." in out.getvalue()
