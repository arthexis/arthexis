from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from core.mcp import auto_start


class ScheduleAutoStartInitializationTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.pytest_patch = mock.patch.dict(
            auto_start.os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False
        )
        self.pytest_patch.start()
        self.argv_patch = mock.patch(
            "core.mcp.auto_start.sys.argv", ["manage.py", "runserver", "--noreload"]
        )
        self.argv_patch.start()

    def tearDown(self):
        self.argv_patch.stop()
        self.pytest_patch.stop()
        super().tearDown()

    def test_defers_database_lookup_during_initialization(self):
        timer_instance = mock.Mock()
        timer_instance.daemon = False

        with mock.patch("core.mcp.auto_start._has_active_assistant_profile") as has_profile:
            with mock.patch("core.mcp.auto_start.threading.Timer") as timer:
                timer.return_value = timer_instance

                result = auto_start.schedule_auto_start(check_profiles_immediately=False)

        self.assertTrue(result)
        has_profile.assert_not_called()
        self.assertTrue(timer_instance.daemon)
        timer_instance.start.assert_called_once()
        timer.assert_called_once()
