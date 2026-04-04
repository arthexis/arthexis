from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.core.review_notifications import ReviewNotificationResult, send_review_notification


class ReviewNotificationTests(SimpleTestCase):
    def test_send_review_notification_uses_lcd_when_available(self):
        captured: dict[str, object] = {}

        def _fake_git(base_dir, *args):
            if args[:2] == ("branch", "--show-current"):
                return "main\n"
            if args[:3] == ("status", "--porcelain", "--untracked-files=all"):
                return " M upgrade.sh\n?? skills/lcd-review-notify/SKILL.md\n"
            return ""

        def _fake_notify(
            *,
            subject,
            body,
            sticky=False,
            expires_at=None,
            channel_type=None,
            **_kwargs,
        ):
            captured.update(
                {
                    "subject": subject,
                    "body": body,
                    "sticky": sticky,
                    "expires_at": expires_at,
                    "channel_type": channel_type,
                }
            )
            return True

        with TemporaryDirectory() as tmpdir, \
            patch("apps.core.review_notifications._run_git", side_effect=_fake_git), \
            patch("apps.core.review_notifications.lcd_feature_enabled", return_value=True), \
            patch("apps.core.review_notifications.notify", side_effect=_fake_notify):
            result = send_review_notification(base_dir=Path(tmpdir))

        self.assertEqual(
            result,
            ReviewNotificationResult(
                subject="Codex ready",
                body="2 files changed",
                branch="main",
                changed_file_count=2,
                used_lcd=True,
                skipped=False,
            ),
        )
        self.assertEqual(captured["subject"], "Codex ready")
        self.assertEqual(captured["body"], "2 files changed")
        self.assertIs(captured["sticky"], True)
        self.assertIsNotNone(captured["expires_at"])
        self.assertEqual(captured["channel_type"], "event")

    def test_send_review_notification_falls_back_cleanly_when_lcd_unavailable(self):
        captured: dict[str, object] = {}

        def _fake_git(base_dir, *args):
            if args[:2] == ("branch", "--show-current"):
                return "main\n"
            if args[:3] == ("status", "--porcelain", "--untracked-files=all"):
                return " M upgrade.sh\n"
            return ""

        def _fake_notify(
            *,
            subject,
            body,
            sticky=False,
            expires_at=None,
            channel_type=None,
            **_kwargs,
        ):
            captured.update(
                {
                    "subject": subject,
                    "body": body,
                    "sticky": sticky,
                    "expires_at": expires_at,
                    "channel_type": channel_type,
                }
            )
            return True

        with TemporaryDirectory() as tmpdir, \
            patch("apps.core.review_notifications._run_git", side_effect=_fake_git), \
            patch("apps.core.review_notifications.lcd_feature_enabled", return_value=False), \
            patch("apps.core.review_notifications.notify", side_effect=_fake_notify):
            result = send_review_notification(base_dir=Path(tmpdir), actor="Manual")

        self.assertFalse(result.used_lcd)
        self.assertEqual(result.subject, "Manual ready")
        self.assertEqual(result.body, "1 file changed")
        self.assertIs(captured["sticky"], True)
        self.assertIsNotNone(captured["expires_at"])
        self.assertEqual(captured["channel_type"], "event")

    def test_send_review_notification_skips_without_reviewable_changes(self):
        notify_calls: list[dict[str, object]] = []

        def _fake_git(base_dir, *args):
            if args[:2] == ("branch", "--show-current"):
                return "main\n"
            if args[:3] == ("status", "--porcelain", "--untracked-files=all"):
                return ""
            return ""

        def _fake_notify(**kwargs):
            notify_calls.append(kwargs)
            return True

        with TemporaryDirectory() as tmpdir, \
            patch("apps.core.review_notifications._run_git", side_effect=_fake_git), \
            patch("apps.core.review_notifications.notify", side_effect=_fake_notify):
            result = send_review_notification(base_dir=Path(tmpdir))

        self.assertTrue(result.skipped)
        self.assertEqual(result.changed_file_count, 0)
        self.assertEqual(notify_calls, [])


class ReviewNotifyCommandTests(SimpleTestCase):
    def test_review_notify_command_reports_fallback_transport(self):
        stdout = io.StringIO()

        with patch(
            "apps.core.management.commands.review_notify.send_review_notification",
            return_value=ReviewNotificationResult(
                subject="Codex ready",
                body="2 files changed",
                branch="main",
                changed_file_count=2,
                used_lcd=False,
                skipped=False,
            ),
        ):
            call_command("review_notify", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("fallback notification (LCD unavailable)", output)
        self.assertIn("branch=main", output)

    def test_review_notify_command_reports_skip(self):
        stdout = io.StringIO()

        with patch(
            "apps.core.management.commands.review_notify.send_review_notification",
            return_value=ReviewNotificationResult(
                subject="Codex ready",
                body="0 files changed",
                branch="main",
                changed_file_count=0,
                used_lcd=False,
                skipped=True,
            ),
        ):
            call_command("review_notify", stdout=stdout)

        self.assertIn("Skipped review notification", stdout.getvalue())
