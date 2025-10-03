import email
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import TestCase, RequestFactory
from django.core.exceptions import ValidationError

from core.admin import EmailInboxAdmin
from teams.models import EmailInbox
from core.models import User


class DummyIMAPSearch:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.messages = {
            b"1": email.message_from_string(
                "Subject: Hello there\nFrom: a@example.com\n\nBody one"
            ).as_bytes(),
            b"2": email.message_from_string(
                "Subject: Other\nFrom: b@example.com\n\nSecond body"
            ).as_bytes(),
        }

    def login(self, username, password):
        pass

    def select(self, mailbox):
        return "OK", [b"1", b"2"]

    def search(self, charset, *criteria):
        subj = frm = txt = None
        i = 0
        while i < len(criteria):
            key = criteria[i]
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="ignore")
            if key == "SUBJECT":
                value = criteria[i + 1]
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                subj = value.strip('"')
                i += 2
            elif key == "FROM":
                value = criteria[i + 1]
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                frm = value.strip('"')
                i += 2
            elif key == "TEXT":
                value = criteria[i + 1]
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                txt = value.strip('"')
                i += 2
            else:
                i += 1
        matches = []
        for mid, msg_bytes in self.messages.items():
            msg = email.message_from_bytes(msg_bytes)
            if subj and subj.lower() not in (msg["Subject"] or "").lower():
                continue
            if frm and frm.lower() not in (msg["From"] or "").lower():
                continue
            body = msg.get_payload()
            if txt and txt.lower() not in body.lower():
                continue
            matches.append(mid)
        return "OK", [b" ".join(matches)]

    def fetch(self, mid, parts):
        return "OK", [(None, self.messages[mid])]

    def logout(self):
        pass


class EmailInboxSearchTests(TestCase):
    @patch("imaplib.IMAP4_SSL", new=lambda h, p: DummyIMAPSearch(h, p))
    def test_search_messages_imap(self):
        user = User.objects.create(username="imap")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.test",
            port=993,
            username="u",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        results = inbox.search_messages(subject="Hello", from_address="a@", body="Body")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"], "Hello there")
        self.assertIn("date", results[0])

    @patch("imaplib.IMAP4_SSL", new=lambda h, p: DummyIMAPSearch(h, p))
    def test_search_messages_imap_with_regex(self):
        user = User.objects.create(username="imapre")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.test",
            port=993,
            username="u",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        results = inbox.search_messages(
            subject="^Hello", use_regular_expressions=True, limit=5
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"], "Hello there")

    def test_invalid_regex_raises_validation_error(self):
        user = User.objects.create(username="regex")
        inbox = EmailInbox.objects.create(
            user=user,
            host="imap.test",
            port=993,
            username="u",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        with self.assertRaises(ValidationError):
            inbox.search_messages(subject="(", use_regular_expressions=True)

    @patch.object(
        EmailInbox,
        "search_messages",
        return_value=[{"subject": "S", "from": "F", "body": "B", "date": ""}],
    )
    def test_admin_action(self, mock_search):
        admin = User.objects.create(username="admin", is_staff=True, is_superuser=True)
        inbox = EmailInbox.objects.create(
            user=admin,
            host="imap.test",
            port=993,
            username="u",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        site = AdminSite()
        ma = EmailInboxAdmin(EmailInbox, site)
        factory = RequestFactory()
        request = factory.post("/", {"apply": "yes", "subject": "S"})
        request.user = admin
        response = ma.search_inbox(request, EmailInbox.objects.filter(id=inbox.id))
        self.assertEqual(response.status_code, 200)
        content = response.render().content.decode()
        self.assertIn("S", content)
        mock_search.assert_called_once_with(
            subject="S", from_address="", body="", use_regular_expressions=False
        )
