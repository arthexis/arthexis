from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db.utils import OperationalError
from django.test import SimpleTestCase

from apps.ocpp import maintenance


class OcppStartupMaintenanceTests(SimpleTestCase):
    def test_reset_cached_statuses_returns_zero_when_table_lookup_fails(self):
        connection = MagicMock()
        connection.introspection.table_names.side_effect = OperationalError

        with patch.object(maintenance, "connections", {"default": connection}):
            with patch.object(maintenance, "logger") as logger:
                cleared = maintenance.reset_cached_statuses()

        assert cleared == 0
        logger.debug.assert_called_once()

    def test_reset_cached_statuses_command_reports_result(self):
        out = StringIO()
        with patch("apps.ocpp.management.commands.reset_cached_statuses.reset_cached_statuses", return_value=3):
            call_command("reset_cached_statuses", stdout=out)

        assert "Cleared cached charger statuses for 3 charge points." in out.getvalue()
