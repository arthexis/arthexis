import email
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import TestCase, RequestFactory

from core.admin import EmailInboxAdmin, EmailInbox as AdminEmailInbox
from core.models import EmailInbox, User


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
        pass

    def search(self, charset, *criteria):
        subj = frm = txt = None
        i = 0
        while i < len(criteria):
            key = criteria[i]
            if key == "SUBJECT":
                subj = criteria[i + 1].strip('"')
                i += 2
            elif key == "FROM":
                frm = criteria[i + 1].strip('"')
                i += 2
            elif key == "TEXT":
                txt = criteria[i + 1].strip('"')
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

    @patch.object(EmailInbox, "search_messages", return_value=[{"subject": "S", "from": "F", "body": "B"}])
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
        ma = EmailInboxAdmin(AdminEmailInbox, site)
        factory = RequestFactory()
        request = factory.post("/", {"apply": "yes", "subject": "S"})
        request.user = admin
        response = ma.search_inbox(
            request, AdminEmailInbox.objects.filter(id=inbox.id)
        )
        self.assertEqual(response.status_code, 200)
        content = response.render().content.decode()
        self.assertIn("S", content)
        mock_search.assert_called_once_with(subject="S", from_address="", body="")
