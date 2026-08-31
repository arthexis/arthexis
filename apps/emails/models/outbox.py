import logging

from django.conf import settings
from django.core.mail import get_connection
from django.db import models

from apps.emails import mailer
from apps.nodes.models import Node
from apps.users.models import Profile as CoreProfile
from apps.sigils.fields import SigilShortAutoField

logger = logging.getLogger(__name__)


class EmailOutbox(CoreProfile):
    """SMTP credentials for sending mail."""

    owner_required = True
    profile_fields = (
        "host",
        "port",
        "username",
        "password",
        "use_tls",
        "use_ssl",
        "from_email",
        "priority",
    )

    node = models.OneToOneField(
        Node,
        on_delete=models.CASCADE,
        related_name="email_outbox",
        null=True,
        blank=True,
    )
    host = SigilShortAutoField(
        max_length=100,
        help_text=("Gmail: smtp.gmail.com. " "GoDaddy: smtpout.secureserver.net"),
    )
    port = models.PositiveIntegerField(
        default=587,
        help_text=("Gmail: 587 (TLS). " "GoDaddy: 587 (TLS) or 465 (SSL)"),
    )
    username = SigilShortAutoField(
        max_length=100,
        blank=True,
        help_text="Full email address for Gmail or GoDaddy",
    )
    password = SigilShortAutoField(
        max_length=100,
        blank=True,
        help_text="Email account password or app password",
    )
    use_tls = models.BooleanField(
        default=True,
        help_text="Check for Gmail or GoDaddy on port 587",
    )
    use_ssl = models.BooleanField(
        default=False,
        help_text="Check for GoDaddy on port 465; Gmail does not use SSL",
    )
    from_email = SigilShortAutoField(
        blank=True,
        verbose_name="From Email",
        max_length=254,
        help_text="Default From address; usually the same as username",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to remove this outbox from automatic selection.",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher values are selected first when multiple outboxes are available.",
    )

    class Meta:
        verbose_name = "Email Outbox"
        verbose_name_plural = "Email Outboxes"
        db_table = "nodes_emailoutbox"
        ordering = ["-priority", "id"]

    def __str__(self) -> str:
        username = (self.username or "").strip()
        if username:
            return username
        return super().__str__()

    def get_connection(self):
        backend_path = getattr(
            settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
        )
        return get_connection(
            backend_path,
            host=self.host,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            use_tls=self.use_tls,
            use_ssl=self.use_ssl,
        )

    def send_mail(self, subject, message, recipient_list, from_email=None, **kwargs):
        from_email = from_email or self.from_email or settings.DEFAULT_FROM_EMAIL
        logger.info("EmailOutbox %s queueing email to %s", self.pk, recipient_list)
        return mailer.send(
            subject,
            message,
            recipient_list,
            from_email,
            outbox=self,
            **kwargs,
        )

    def owner_display(self):
        owner = super().owner_display()
        if owner:
            return owner
        return str(self.node) if self.node_id else ""

    def is_ready(self) -> bool:
        try:
            connection = self.get_connection()
            connection.open()
            connection.close()
            return True
        except Exception:
            logger.warning(
                "EmailOutbox %s failed readiness check", self.pk, exc_info=True
            )
            return False
