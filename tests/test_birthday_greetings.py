from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core import mail

from core.tasks import birthday_greetings
from nodes.models import NetMessage


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BirthdayGreetingsTaskTests(TestCase):
    def test_birthday_greetings_sends_net_message_and_email(self):
        User = get_user_model()
        today = timezone.localdate()
        user = User.objects.create_user(
            username="alice",
            password="x",
            birthday=today,
            email="alice@example.com",
        )
        initial = NetMessage.objects.count()
        birthday_greetings()
        self.assertEqual(NetMessage.objects.count(), initial + 1)
        msg = NetMessage.objects.order_by("-created").first()
        self.assertEqual(msg.subject, "Happy bday!")
        self.assertEqual(msg.body, user.username)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Happy bday!", email.subject)
        self.assertIn(user.username, email.body)
