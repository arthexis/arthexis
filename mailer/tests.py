from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from .models import EmailTemplate, QueuedEmail
from .views import send_queued


class MailerTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.template = EmailTemplate.objects.create(
            name="welcome", subject="Hello {name}", body="Hi {name}!"
        )

    def test_queue_and_send_email(self):
        response = self.client.post(
            reverse("queue-email"),
            data={"to": "a@example.com", "template_id": self.template.id, "context": {"name": "Bob"}},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        qid = response.json()["id"]
        self.assertEqual(QueuedEmail.objects.filter(id=qid).count(), 1)

        send_queued()
        qe = QueuedEmail.objects.get(id=qid)
        self.assertTrue(qe.sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Hello Bob", mail.outbox[0].subject)

    def test_purge_sent(self):
        qe = QueuedEmail.objects.create(to="x@example.com", template=self.template)
        qe.mark_sent()
        response = self.client.post(reverse("purge-queue"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["purged"], 1)
        self.assertEqual(QueuedEmail.objects.count(), 0)
