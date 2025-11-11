import base64

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import EmailTransaction, EmailTransactionAttachment, User
from teams.models import EmailCollector, EmailInbox, EmailOutbox


@pytest.mark.role("Satellite")
@pytest.mark.role("Watchtower")
class EmailTransactionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner")
        self.inbox = EmailInbox.objects.create(
            user=self.user,
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="password",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        self.collector = EmailCollector.objects.create(
            inbox=self.inbox,
            name="Collector",
        )
        self.outbox = EmailOutbox.objects.create(
            user=self.user,
            host="smtp.example.com",
            port=587,
            username="user@example.com",
            password="password",
            use_tls=True,
            use_ssl=False,
            from_email="user@example.com",
        )

    def test_inbound_transaction_requires_source(self):
        transaction = EmailTransaction(
            direction=EmailTransaction.INBOUND,
            status=EmailTransaction.STATUS_COLLECTED,
            collector=self.collector,
            inbox=self.inbox,
            subject="Hello",
            from_address="sender@example.com",
            to_addresses=["team@example.com"],
            body_text="Body",
        )
        transaction.full_clean()
        transaction.save()

        assert transaction.pk is not None
        assert transaction.direction == EmailTransaction.INBOUND

    def test_outbound_transaction_accepts_outbox(self):
        transaction = EmailTransaction(
            direction=EmailTransaction.OUTBOUND,
            status=EmailTransaction.STATUS_QUEUED,
            outbox=self.outbox,
            subject="Queued",
            to_addresses=["customer@example.com"],
        )
        transaction.full_clean()
        transaction.save()

        assert transaction.status == EmailTransaction.STATUS_QUEUED
        assert transaction.outbox == self.outbox

    def test_transaction_without_association_is_invalid(self):
        transaction = EmailTransaction(
            direction=EmailTransaction.INBOUND,
            status=EmailTransaction.STATUS_COLLECTED,
            subject="Invalid",
        )
        with pytest.raises(ValidationError):
            transaction.full_clean()

    def test_attachments_are_persisted(self):
        transaction = EmailTransaction.objects.create(
            direction=EmailTransaction.INBOUND,
            status=EmailTransaction.STATUS_COLLECTED,
            inbox=self.inbox,
            subject="Attachment",
        )
        attachment = EmailTransactionAttachment.objects.create(
            transaction=transaction,
            filename="example.txt",
            content_type="text/plain",
            content=base64.b64encode(b"example").decode(),
        )

        assert transaction.attachments.count() == 1
        assert transaction.attachments.first() == attachment
        assert attachment.inline is False
