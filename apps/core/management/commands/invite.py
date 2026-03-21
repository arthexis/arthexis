from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.models import InviteLead
from apps.emails import mailer
from apps.nodes.models import Node


class Command(BaseCommand):
    """Send invitation links for users matching the provided email address."""

    help = "Send an invitation link and display it in the console"

    def add_arguments(self, parser):
        """Register positional argument for the target email address."""

        parser.add_argument("email", help="Email address to send the invitation to")

    def _build_link(self, node: Node | None, path: str) -> str:
        """Build an invitation link from public base URL settings with secure defaults."""

        public_base_url = getattr(settings, "PUBLIC_BASE_URL", "").strip()
        if public_base_url:
            base = public_base_url if public_base_url.endswith("/") else f"{public_base_url}/"
            return urljoin(base, path.lstrip("/"))

        if node and node.hostname:
            host = node.hostname
            if node.port:
                host = f"{host}:{node.port}"
            return f"https://{host}{path}"

        return path

    def handle(self, *args, **options):
        """Send invitation links and mark unsent InviteLead rows as sent."""

        email = options["email"]
        user_model = get_user_model()
        users = list(user_model.objects.filter(email__iexact=email))
        if not users:
            raise CommandError(f"No user found with email {email}")

        node = Node.get_local()
        used_outbox = None
        if node and getattr(node, "email_outbox_id", None):
            used_outbox = node.email_outbox

        for user in users:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            path = reverse("pages:invitation-login", args=[uid, token])
            link = self._build_link(node, path)

            subject = "Your invitation link"
            body = f"Use the following link to access your account: {link}"
            try:
                if node and getattr(node, "email_outbox_id", None):
                    result = mailer.send(
                        subject,
                        body,
                        [email],
                        node=node,
                        outbox=node.email_outbox,
                    )
                    used_outbox = getattr(result, "outbox", None) or node.email_outbox
                else:
                    send_mail(subject, body, None, [email])
            except RuntimeError as exc:  # pragma: no cover - depends on outbox configuration
                self.stderr.write(self.style.WARNING(f"Email send failed: {exc}"))
                send_mail(subject, body, None, [email])

            self.stdout.write(link)

        InviteLead.objects.filter(email__iexact=email, sent_on__isnull=True).update(
            sent_on=timezone.now(),
            sent_via_outbox=used_outbox,
        )
        self.stdout.write(self.style.SUCCESS("Invitation sent"))
