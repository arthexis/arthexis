from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from core.mcp import auto_start
from core.models import AssistantProfile, hash_key


class ShouldScheduleAutoStartTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        # Ensure the pytest guard does not short-circuit the logic during tests.
        self.pytest_patch = mock.patch.dict(
            auto_start.os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False
        )
        self.pytest_patch.start()

    def tearDown(self):
        self.pytest_patch.stop()
        super().tearDown()

    def test_runserver_child_process_schedules(self):
        with mock.patch("core.mcp.auto_start.sys.argv", ["manage.py", "runserver"]):
            with mock.patch.dict(
                auto_start.os.environ, {"RUN_MAIN": "true"}, clear=False
            ):
                self.assertTrue(auto_start._should_schedule_auto_start())

    def test_runserver_parent_process_skipped(self):
        with mock.patch("core.mcp.auto_start.sys.argv", ["manage.py", "runserver"]):
            with mock.patch.dict(
                auto_start.os.environ, {"RUN_MAIN": "false"}, clear=False
            ):
                self.assertFalse(auto_start._should_schedule_auto_start())

    def test_runserver_no_reload_schedules(self):
        argv = ["manage.py", "runserver", "--noreload"]
        with mock.patch("core.mcp.auto_start.sys.argv", argv):
            self.assertTrue(auto_start._should_schedule_auto_start())

    def test_management_command_skip(self):
        with mock.patch("core.mcp.auto_start.sys.argv", ["manage.py", "migrate"]):
            self.assertFalse(auto_start._should_schedule_auto_start())

    def test_pytest_command_skipped(self):
        with mock.patch("core.mcp.auto_start.sys.argv", ["pytest"]):
            with mock.patch.dict(
                auto_start.os.environ, {"PYTEST_CURRENT_TEST": "running"}, clear=False
            ):
                self.assertFalse(auto_start._should_schedule_auto_start())

    def test_celery_command_skipped(self):
        with mock.patch("core.mcp.auto_start.sys.argv", ["celery", "worker"]):
            self.assertFalse(auto_start._should_schedule_auto_start())

    def test_generic_process_allows_auto_start(self):
        with mock.patch("core.mcp.auto_start.sys.argv", ["gunicorn", "config.wsgi"]):
            self.assertTrue(auto_start._should_schedule_auto_start())


class HasActiveAssistantProfileTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="assistant-test-user", password="password"
        )

    def test_returns_true_when_active_profile_exists(self):
        AssistantProfile.objects.create(
            user=self.user,
            user_key_hash=hash_key("active"),
            scopes=["chat"],
            is_active=True,
        )
        self.assertTrue(auto_start._has_active_assistant_profile())

    def test_returns_false_when_all_profiles_inactive(self):
        AssistantProfile.objects.create(
            user=self.user,
            user_key_hash=hash_key("inactive"),
            scopes=["chat"],
            is_active=False,
        )
        self.assertFalse(auto_start._has_active_assistant_profile())


class ScheduleAutoStartTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="assistant-schedule-user", password="password"
        )
        self.profile = AssistantProfile.objects.create(
            user=self.user,
            user_key_hash=hash_key("schedule"),
            scopes=["chat"],
            is_active=True,
        )
        self.pytest_patch = mock.patch.dict(
            auto_start.os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False
        )
        self.pytest_patch.start()
        self.argv_patch = mock.patch(
            "core.mcp.auto_start.sys.argv", ["manage.py", "runserver", "--noreload"]
        )
        self.argv_patch.start()

    def tearDown(self):
        self.pytest_patch.stop()
        self.argv_patch.stop()
        super().tearDown()

    @mock.patch("core.mcp.auto_start.threading.Timer")
    def test_schedule_auto_start_creates_timer(self, timer_mock):
        timer_instance = timer_mock.return_value
        result = auto_start.schedule_auto_start(delay=1.5)
        self.assertTrue(result)
        timer_mock.assert_called_once()
        args, kwargs = timer_mock.call_args
        self.assertAlmostEqual(args[0], 1.5)
        callback = args[1]
        self.assertTrue(callable(callback))
        self.assertTrue(timer_instance.daemon)
        timer_instance.start.assert_called_once()

    @mock.patch("core.mcp.auto_start.threading.Timer")
    def test_schedule_auto_start_skips_when_no_active_profile(self, timer_mock):
        AssistantProfile.objects.update(is_active=False)
        result = auto_start.schedule_auto_start(delay=1.0)
        self.assertFalse(result)
        timer_mock.assert_not_called()

    @mock.patch("core.mcp.auto_start.threading.Timer")
    def test_schedule_auto_start_defers_profile_check_when_requested(
        self, timer_mock
    ):
        AssistantProfile.objects.update(is_active=False)
        timer_instance = timer_mock.return_value

        result = auto_start.schedule_auto_start(
            delay=1.0, check_profiles_immediately=False
        )

        self.assertTrue(result)
        timer_mock.assert_called_once()
        timer_instance.start.assert_called_once()

    @mock.patch("core.mcp.auto_start.mcp_process")
    def test_start_server_if_needed_starts_when_inactive(self, process_mock):
        process_mock.get_status.return_value = {"running": False}
        with mock.patch("core.mcp.auto_start._has_active_assistant_profile", return_value=True):
            self.assertTrue(auto_start._start_server_if_needed())
        process_mock.start_server.assert_called_once()

    @mock.patch("core.mcp.auto_start.mcp_process")
    def test_start_server_if_needed_skips_when_running(self, process_mock):
        process_mock.get_status.return_value = {"running": True}
        with mock.patch("core.mcp.auto_start._has_active_assistant_profile", return_value=True):
            self.assertFalse(auto_start._start_server_if_needed())
        process_mock.start_server.assert_not_called()

    @mock.patch("core.mcp.auto_start.mcp_process")
    def test_start_server_if_needed_handles_errors(self, process_mock):
        process_mock.get_status.side_effect = RuntimeError("boom")
        with mock.patch("core.mcp.auto_start._has_active_assistant_profile", return_value=True):
            self.assertFalse(auto_start._start_server_if_needed())
        process_mock.start_server.assert_not_called()
