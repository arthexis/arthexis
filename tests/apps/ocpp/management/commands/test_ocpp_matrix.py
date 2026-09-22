from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.ocpp.protocol.registry import ALL_ACTIONS


class OcppMatrixCommandTests(SimpleTestCase):
    def test_app_wide_report_states_the_complete_matrix(self) -> None:
        output = StringIO()

        call_command("ocpp_matrix", stdout=output)

        self.assertIn(
            f"{len(ALL_ACTIONS)} / {len(ALL_ACTIONS)} implemented",
            output.getvalue(),
        )
