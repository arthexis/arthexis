import os
from unittest.mock import patch

import django
from django.core import mail
from django.test import TestCase, override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core import mailer
from core.models import EmailTransaction, SecurityGroup, User
from teams.models import EmailOutbox


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MailerSendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner")

    def _outbox(self, *, attach_group: bool = False, **kwargs):
        defaults = {
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "password",
            "use_tls": True,
            "use_ssl": False,
            "from_email": "user@example.com",
        }
        if attach_group:
            group = SecurityGroup.objects.create(name=f"group-{EmailOutbox.objects.count()+1}")
            self.user.groups.add(group)
            defaults["group"] = group
        else:
            defaults["user"] = self.user
        defaults.update(kwargs)
        return EmailOutbox.objects.create(**defaults)

    def test_can_send_email_detects_outbox(self):
        self._outbox()
        assert mailer.can_send_email() is True

    def test_can_send_email_returns_false_without_outbox(self):
        assert mailer.can_send_email() is False

    def test_send_allows_two_item_attachment(self):
        outbox = self._outbox()
        mail.outbox.clear()

        email = mailer.send(
            "Subject",
            "Body",
            ["person@example.com"],
            attachments=[("report.txt", "hello world")],
            fail_silently=True,
            outbox=outbox,
        )

        attachment = email.attachments[0]
        assert attachment.filename == "report.txt"
        assert attachment.content == "hello world"
        assert attachment.mimetype == "text/plain"

        stored_attachment = mail.outbox[0].attachments[0]
        assert stored_attachment.filename == "report.txt"
        assert stored_attachment.content == "hello world"
        assert stored_attachment.mimetype == "text/plain"

        transaction = EmailTransaction.objects.filter(subject="Subject").first()
        assert transaction is not None
        assert transaction.outbox == outbox
        assert transaction.status == EmailTransaction.STATUS_SENT

    def test_send_retries_with_next_outbox_on_failure(self):
        primary = self._outbox(priority=10, from_email="primary@example.com")
        fallback = self._outbox(
            attach_group=True, priority=5, from_email="fallback@example.com"
        )

        with patch(
            "core.mailer._candidate_outboxes", return_value=[primary, fallback]
        ):
            with patch.object(primary, "get_connection", side_effect=Exception("fail")):
                email = mailer.send(
                    "Retry",
                    "Body",
                    ["person@example.com"],
                    user=self.user,
                )

        transactions = EmailTransaction.objects.filter(subject="Retry")
        statuses = {
            (txn.outbox_id, txn.status)
            for txn in transactions.select_related("outbox")
        }
        assert (primary.id, EmailTransaction.STATUS_FAILED) in statuses
        assert any(outbox_id == fallback.id for outbox_id, _ in statuses)

    def test_priority_selects_highest_outbox(self):
        self._outbox(priority=1, from_email="lower@example.com")
        higher = self._outbox(
            attach_group=True, priority=5, from_email="higher@example.com"
        )

        mail.outbox.clear()
        email = mailer.send(
            "Priority",
            "Body",
            ["person@example.com"],
            user=self.user,
        )

        assert getattr(email, "outbox", None) == higher
        assert mail.outbox[0].from_email == "higher@example.com"
