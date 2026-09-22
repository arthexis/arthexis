from django.test import SimpleTestCase

from arthexis.settings import CELERY_BEAT_SCHEDULE


class OcppSchedulingTests(SimpleTestCase):
    def test_ocpp_schedules_do_not_dispatch_charger_operations(self) -> None:
        scheduled_tasks = {
            str(schedule["task"]) for schedule in CELERY_BEAT_SCHEDULE.values()
        }

        self.assertFalse(any("charger" in task for task in scheduled_tasks))
