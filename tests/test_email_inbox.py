import imaplib
import poplib
import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from unittest.mock import patch

from core.models import User, EmailInbox


class DummyIMAP:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def login(self, username, password):
        if username == "bad":
            raise Exception("fail")

    def logout(self):
        pass


class DummyPOP:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def user(self, username):
        if username == "bad":
            raise Exception("fail")

    def pass_(self, password):
        if password == "bad":
            raise Exception("fail")

    def quit(self):
        pass


@pytest.mark.role("Gateway")
@pytest.mark.role("Constellation")
class EmailInboxTests(TestCase):
    @patch("imaplib.IMAP4_SSL", new=lambda h, p: DummyIMAP(h, p))
    def test_imap_connection_success(self):
        user = User.objects.create(username="imap")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.test",
            port=993,
            username="good",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        assert inbox.test_connection() is True

    @patch("poplib.POP3_SSL", new=lambda h, p: DummyPOP(h, p))
    def test_pop_connection_success(self):
        user = User.objects.create(username="pop")
        inbox = EmailInbox.objects.create(
            user=user,
            host="pop.test",
            port=995,
            username="good",
            password="p",
            protocol=EmailInbox.POP3,
            use_ssl=True,
        )
        assert inbox.test_connection() is True

    @patch("imaplib.IMAP4_SSL", new=lambda h, p: DummyIMAP(h, p))
    def test_connection_failure(self):
        user = User.objects.create(username="bad")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.test",
            port=993,
            username="bad",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        with pytest.raises(ValidationError):
            inbox.test_connection()
