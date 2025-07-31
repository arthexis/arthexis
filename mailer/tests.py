from django.core import mail as django_mail
from django.test import Client, TestCase
from django.urls import reverse
from post_office import mail
from post_office.mail import send_queued
from post_office.models import Email, STATUS

from .models import EmailTemplate


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
        self.assertEqual(Email.objects.filter(id=qid).count(), 1)

        send_queued()
        email = Email.objects.get(id=qid)
        self.assertEqual(email.status, STATUS.sent)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("Hello Bob", django_mail.outbox[0].subject)

    def test_purge_sent(self):
        mail.send(recipients=["x@example.com"], subject="Hi", message="Hello")
        send_queued()
        response = self.client.post(reverse("purge-queue"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["purged"], 1)
        self.assertEqual(Email.objects.count(), 0)
