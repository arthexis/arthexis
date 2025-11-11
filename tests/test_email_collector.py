from django.test import TestCase
from unittest.mock import patch

from core.models import User, EmailArtifact
from teams.models import EmailInbox, EmailCollector
from core.tasks import poll_email_collectors


class EmailCollectorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="u")
        self.inbox = EmailInbox.objects.create(
            user=self.user,
            host="imap.test",
            port=993,
            username="u",
            password="p",
            protocol=EmailInbox.IMAP,
        )

    def test_collect_creates_artifact_and_stops_on_existing(self):
        collector = EmailCollector.objects.create(
            inbox=self.inbox,
            fragment="code is [code]",
        )
        existing = {
            "subject": "Old",
            "from": "a@test",
            "body": "code is 123",
        }
        EmailArtifact.objects.create(
            collector=collector,
            subject=existing["subject"],
            sender=existing["from"],
            body=existing["body"],
            sigils={"code": "123"},
            fingerprint=EmailArtifact.fingerprint_for(
                existing["subject"], existing["from"], existing["body"]
            ),
        )
        messages = [
            {"subject": "New", "from": "a@test", "body": "code is 456"},
            existing,
            {"subject": "Older", "from": "a@test", "body": "code is 789"},
        ]
        with patch.object(EmailInbox, "search_messages", return_value=messages) as mock_search:
            collector.collect()
            mock_search.assert_called_once_with(
                subject="",
                from_address="",
                body="",
                limit=10,
                use_regular_expressions=False,
            )
        artifacts = EmailArtifact.objects.filter(collector=collector)
        assert artifacts.count() == 2
        new_artifact = artifacts.get(subject="New")
        assert new_artifact.sigils == {"code": "456"}
        assert not artifacts.filter(subject="Older").exists()

    def test_poll_task_invokes_collectors(self):
        collector = EmailCollector.objects.create(inbox=self.inbox)
        with patch.object(EmailCollector, "collect") as mock_collect:
            poll_email_collectors()
            mock_collect.assert_called_once_with()

    def test_collect_with_regular_expressions(self):
        collector = EmailCollector.objects.create(
            inbox=self.inbox,
            use_regular_expressions=True,
        )
        with patch.object(EmailInbox, "search_messages", return_value=[]) as mock_search:
            collector.collect(limit=5)
        mock_search.assert_called_once_with(
            subject="",
            from_address="",
            body="",
            limit=5,
            use_regular_expressions=True,
        )

    def test_string_representation_prefers_name(self):
        collector = EmailCollector.objects.create(
            inbox=self.inbox,
            name="Invoices",
        )
        self.assertEqual(str(collector), "Invoices")
