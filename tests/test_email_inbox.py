import email
import imaplib
import poplib
import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from unittest.mock import patch

from core.models import User
from teams.models import EmailInbox


class DummyIMAP:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def login(self, username, password):
        if username == "bad":
            raise Exception("fail")

    def logout(self):
        pass


class DummyIMAPSelectError(DummyIMAP):
    def __init__(self, host, port):
        super().__init__(host, port)
        self.logout_called = False

    def select(self, mailbox):
        return (
            "NO",
            [b"[TRYCREATE] No data in .Sent (0.001 + 0.000 secs)"]
        )

    def logout(self):
        self.logout_called = True


class DummyIMAPUnicode(DummyIMAP):
    def __init__(self, host, port):
        super().__init__(host, port)
        self.search_args = None
        self._message = email.message_from_string(
            "Subject: Café Response\nFrom: sender@example.com\n\nBody"
        ).as_bytes()

    def select(self, mailbox):
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.search_args = (charset, criteria)
        return "OK", [b"1"]

    def fetch(self, mid, parts):
        return "OK", [(None, self._message)]


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


@pytest.mark.role("Satellite")
@pytest.mark.role("Watchtower")
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

    def test_search_messages_handles_select_error(self):
        user = User.objects.create(username="imap-select")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.test",
            port=993,
            username="good",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        dummy = DummyIMAPSelectError(inbox.host, inbox.port)

        with patch("imaplib.IMAP4_SSL", new=lambda h, p: dummy):
            with pytest.raises(ValidationError) as excinfo:
                inbox.search_messages()

        assert "No data in .Sent" in str(excinfo.value)
        assert dummy.logout_called is True

    def test_search_messages_quotes_non_ascii_filters(self):
        user = User.objects.create(username="imap-unicode")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.test",
            port=993,
            username="good",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )

        dummy = DummyIMAPUnicode(inbox.host, inbox.port)

        with patch("imaplib.IMAP4_SSL", new=lambda h, p: dummy):
            results = inbox.search_messages(subject="Café")

        assert len(results) == 1
        assert results[0]["subject"] == "Café Response"
        assert dummy.search_args is not None
        charset, criteria = dummy.search_args
        assert charset == "UTF-8"
        assert criteria[0] == "SUBJECT"
        assert isinstance(criteria[1], bytes)
        assert criteria[1].startswith(b'"')
        assert criteria[1].endswith(b'"')
        assert b"Caf\xc3\xa9" in criteria[1]

    def test_string_representation_does_not_duplicate_email_hostname(self):
        user = User.objects.create(username="imap-user")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.example.com",
            port=993,
            username="mailer@example.com",
            password="secret",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )

        self.assertEqual(str(inbox), "mailer@example.com")
