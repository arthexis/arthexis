from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from core import mailer
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils import timezone
from django.conf import settings

from nodes.models import Node
from core.models import InviteLead


class Command(BaseCommand):
    """Send an invitation link for a given email."""

    help = "Send an invitation link and display it in the console"

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email address to send the invitation to")

    def handle(self, *args, **options):
        email = options["email"]
        User = get_user_model()
        users = list(User.objects.filter(email__iexact=email))
        if not users:
            raise CommandError(f"No user found with email {email}")

        node = Node.get_local()
        outbox = getattr(node, "email_outbox", None) if node else None
        used_outbox = None

        for user in users:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            path = reverse("pages:invitation-login", args=[uid, token])
            if node:
                link = f"http://{node.hostname}:{node.port}{path}"
            else:
                link = path

            subject = "Your invitation link"
            body = f"Use the following link to access your account: {link}"
            try:
                if node:
                    node.send_mail(subject, body, [email])
                    if outbox:
                        used_outbox = outbox
                else:
                    mailer.send(subject, body, [email], settings.DEFAULT_FROM_EMAIL)
            except Exception as exc:  # pragma: no cover - log failures
                self.stderr.write(self.style.WARNING(f"Email send failed: {exc}"))

            self.stdout.write(link)

        InviteLead.objects.filter(email__iexact=email, sent_on__isnull=True).update(
            sent_on=timezone.now(),
            sent_via_outbox=used_outbox,
        )
        self.stdout.write(self.style.SUCCESS("Invitation sent"))
