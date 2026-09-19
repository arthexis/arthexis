from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.ocpp.domain.matrix import support_matrix, unimplemented_actions
from apps.ocpp.protocol.registry import ALL_ACTIONS
from arthexis.settings import CELERY_BEAT_SCHEDULE


class SupportMatrixTests(SimpleTestCase):
    def test_every_frozen_contract_has_an_executable_handler_or_validator(self) -> None:
        entries = support_matrix()

        self.assertEqual(len(entries), len(ALL_ACTIONS))
        self.assertFalse(unimplemented_actions())

    def test_app_wide_report_states_the_complete_matrix(self) -> None:
        output = StringIO()

        call_command("ocpp_matrix", stdout=output)

        self.assertIn(
            f"{len(ALL_ACTIONS)} / {len(ALL_ACTIONS)} implemented", output.getvalue()
        )

    def test_ocpp_schedules_do_not_dispatch_charger_operations(self) -> None:
        scheduled_tasks = {
            str(schedule["task"]) for schedule in CELERY_BEAT_SCHEDULE.values()
        }

        self.assertFalse(any("charger" in task for task in scheduled_tasks))
