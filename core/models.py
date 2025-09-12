from django.contrib.auth.models import (
    AbstractUser,
    Group,
    UserManager as DjangoUserManager,
)
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError
from django.apps import apps
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from datetime import timedelta
from django.utils.text import slugify
from django.utils.safestring import mark_safe
from django.contrib.contenttypes.models import ContentType
import hashlib
import os
import subprocess
import secrets
import re
from io import BytesIO
from django.core.files.base import ContentFile
import qrcode
import xmlrpc.client
from django.utils import timezone
import uuid
from pathlib import Path
from docutils.core import publish_parts
from django.core import serializers
from urllib.parse import urlparse
from utils import revision as revision_utils

from .entity import Entity, EntityUserManager
from .release import Package as ReleasePackage, Credentials, DEFAULT_PACKAGE
from .user_data import UserDatum  # noqa: F401 - ensure model registration
from .fields import SigilShortAutoField


class SecurityGroup(Group):
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )

    class Meta:
        verbose_name = "Security Group"
        verbose_name_plural = "Security Groups"


class SigilRoot(Entity):
    class Context(models.TextChoices):
        CONFIG = "config", "Configuration"
        ENTITY = "entity", "Entity"

    prefix = models.CharField(max_length=50, unique=True)
    context_type = models.CharField(max_length=20, choices=Context.choices)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.CASCADE
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.prefix

    class Meta:
        verbose_name = "Sigil Root"
        verbose_name_plural = "Sigil Roots"


class CustomSigil(SigilRoot):
    class Meta:
        proxy = True
        app_label = "pages"
        verbose_name = _("Custom Sigil")
        verbose_name_plural = _("Custom Sigils")


class Lead(Entity):
    """Common request lead information."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    path = models.TextField(blank=True)
    referer = models.TextField(blank=True)
    user_agent = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class InviteLead(Lead):
    email = models.EmailField()
    comment = models.TextField(blank=True)
    sent_on = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Invite Lead"
        verbose_name_plural = "Invite Leads"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.email


class Address(Entity):
    """Physical location information for a user."""

    class State(models.TextChoices):
        COAHUILA = "CO", "Coahuila"
        NUEVO_LEON = "NL", "Nuevo León"

    COAHUILA_MUNICIPALITIES = [
        "Abasolo",
        "Acuña",
        "Allende",
        "Arteaga",
        "Candela",
        "Castaños",
        "Cuatro Ciénegas",
        "Escobedo",
        "Francisco I. Madero",
        "Frontera",
        "General Cepeda",
        "Guerrero",
        "Hidalgo",
        "Jiménez",
        "Juárez",
        "Lamadrid",
        "Matamoros",
        "Monclova",
        "Morelos",
        "Múzquiz",
        "Nadadores",
        "Nava",
        "Ocampo",
        "Parras",
        "Piedras Negras",
        "Progreso",
        "Ramos Arizpe",
        "Sabinas",
        "Sacramento",
        "Saltillo",
        "San Buenaventura",
        "San Juan de Sabinas",
        "San Pedro",
        "Sierra Mojada",
        "Torreón",
        "Viesca",
        "Villa Unión",
        "Zaragoza",
    ]

    NUEVO_LEON_MUNICIPALITIES = [
        "Abasolo",
        "Agualeguas",
        "Los Aldamas",
        "Allende",
        "Anáhuac",
        "Apodaca",
        "Aramberri",
        "Bustamante",
        "Cadereyta Jiménez",
        "El Carmen",
        "Cerralvo",
        "Ciénega de Flores",
        "China",
        "Doctor Arroyo",
        "Doctor Coss",
        "Doctor González",
        "Galeana",
        "García",
        "General Bravo",
        "General Escobedo",
        "General Terán",
        "General Treviño",
        "General Zaragoza",
        "General Zuazua",
        "Guadalupe",
        "Los Herreras",
        "Higueras",
        "Hualahuises",
        "Iturbide",
        "Juárez",
        "Lampazos de Naranjo",
        "Linares",
        "Marín",
        "Melchor Ocampo",
        "Mier y Noriega",
        "Mina",
        "Montemorelos",
        "Monterrey",
        "Parás",
        "Pesquería",
        "Los Ramones",
        "Rayones",
        "Sabinas Hidalgo",
        "Salinas Victoria",
        "San Nicolás de los Garza",
        "San Pedro Garza García",
        "Santa Catarina",
        "Santiago",
        "Vallecillo",
        "Villaldama",
        "Hidalgo",
    ]

    MUNICIPALITIES_BY_STATE = {
        State.COAHUILA: COAHUILA_MUNICIPALITIES,
        State.NUEVO_LEON: NUEVO_LEON_MUNICIPALITIES,
    }

    MUNICIPALITY_CHOICES = [
        (name, name) for name in COAHUILA_MUNICIPALITIES + NUEVO_LEON_MUNICIPALITIES
    ]

    street = models.CharField(max_length=255)
    number = models.CharField(max_length=20)
    municipality = models.CharField(max_length=100, choices=MUNICIPALITY_CHOICES)
    state = models.CharField(max_length=2, choices=State.choices)
    postal_code = models.CharField(max_length=10)

    class Meta:
        verbose_name_plural = _("Addresses")

    def clean(self):
        from django.core.exceptions import ValidationError

        allowed = self.MUNICIPALITIES_BY_STATE.get(self.state, [])
        if self.municipality not in allowed:
            raise ValidationError(
                {"municipality": _("Invalid municipality for the selected state")}
            )

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.street} {self.number}, {self.municipality}, {self.state}"


class User(Entity, AbstractUser):
    objects = EntityUserManager()
    all_objects = DjangoUserManager()
    """Custom user model."""

    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Optional contact phone number",
    )
    birthday = models.DateField(null=True, blank=True)
    address = models.ForeignKey(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    has_charger = models.BooleanField(default=False)
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=(
            "Designates whether this user should be treated as active. Unselect this instead of deleting energy accounts."
        ),
    )

    def __str__(self):
        return self.username


class OdooProfile(Entity):
    """Store Odoo API credentials for a user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="odoo_profile",
        on_delete=models.CASCADE,
    )
    host = SigilShortAutoField(max_length=255)
    database = SigilShortAutoField(max_length=255)
    username = SigilShortAutoField(max_length=255)
    password = SigilShortAutoField(max_length=255)
    verified_on = models.DateTimeField(null=True, blank=True)
    odoo_uid = models.PositiveIntegerField(null=True, blank=True, editable=False)
    name = models.CharField(max_length=255, blank=True, editable=False)
    email = models.EmailField(blank=True, editable=False)

    def _clear_verification(self):
        self.verified_on = None
        self.odoo_uid = None
        self.name = ""
        self.email = ""

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).all_objects.get(pk=self.pk)
            if (
                old.username != self.username
                or old.password != self.password
                or old.database != self.database
                or old.host != self.host
            ):
                self._clear_verification()
        super().save(*args, **kwargs)

    @property
    def is_verified(self):
        return self.verified_on is not None

    def verify(self):
        """Check credentials against Odoo and pull user info."""
        common = xmlrpc.client.ServerProxy(f"{self.host}/xmlrpc/2/common")
        uid = common.authenticate(self.database, self.username, self.password, {})
        if not uid:
            self._clear_verification()
            raise ValidationError(_("Invalid Odoo credentials"))
        models_proxy = xmlrpc.client.ServerProxy(f"{self.host}/xmlrpc/2/object")
        info = models_proxy.execute_kw(
            self.database,
            uid,
            self.password,
            "res.users",
            "read",
            [uid],
            {"fields": ["name", "email"]},
        )[0]
        self.odoo_uid = uid
        self.name = info.get("name", "")
        self.email = info.get("email", "")
        self.verified_on = timezone.now()
        self.save(update_fields=["odoo_uid", "name", "email", "verified_on"])
        return True

    def execute(self, model, method, *args, **kwargs):
        """Execute an Odoo RPC call, invalidating credentials on failure."""
        try:
            client = xmlrpc.client.ServerProxy(f"{self.host}/xmlrpc/2/object")
            return client.execute_kw(
                self.database,
                self.odoo_uid,
                self.password,
                model,
                method,
                args,
                kwargs,
            )
        except Exception:
            self._clear_verification()
            self.save(update_fields=["verified_on"])
            raise

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.user} @ {self.host}"

    class Meta:
        verbose_name = _("Odoo Profile")
        verbose_name_plural = _("Odoo Profiles")


class EmailInbox(Entity):
    """Credentials and configuration for connecting to an email mailbox."""

    IMAP = "imap"
    POP3 = "pop3"
    PROTOCOL_CHOICES = [
        (IMAP, "IMAP"),
        (POP3, "POP3"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="email_inboxes",
        on_delete=models.CASCADE,
    )
    username = SigilShortAutoField(
        max_length=255,
        help_text="Login name for the mailbox",
    )
    host = SigilShortAutoField(
        max_length=255,
        help_text=(
            "Examples: Gmail IMAP 'imap.gmail.com', Gmail POP3 'pop.gmail.com',"
            " GoDaddy IMAP 'imap.secureserver.net', GoDaddy POP3 'pop.secureserver.net'"
        ),
    )
    port = models.PositiveIntegerField(
        default=993,
        help_text=(
            "Common ports: Gmail IMAP 993, Gmail POP3 995, "
            "GoDaddy IMAP 993, GoDaddy POP3 995"
        ),
    )
    password = SigilShortAutoField(max_length=255)
    protocol = SigilShortAutoField(
        max_length=5,
        choices=PROTOCOL_CHOICES,
        default=IMAP,
        help_text=(
            "IMAP keeps emails on the server for access across devices; "
            "POP3 downloads messages to a single device and may remove them from the server"
        ),
    )
    use_ssl = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Email Inbox"
        verbose_name_plural = "Email Inboxes"

    def test_connection(self):
        """Attempt to connect to the configured mailbox."""
        try:
            if self.protocol == self.IMAP:
                import imaplib

                conn = (
                    imaplib.IMAP4_SSL(self.host, self.port)
                    if self.use_ssl
                    else imaplib.IMAP4(self.host, self.port)
                )
                conn.login(self.username, self.password)
                conn.logout()
            else:
                import poplib

                conn = (
                    poplib.POP3_SSL(self.host, self.port)
                    if self.use_ssl
                    else poplib.POP3(self.host, self.port)
                )
                conn.user(self.username)
                conn.pass_(self.password)
                conn.quit()
            return True
        except Exception as exc:
            raise ValidationError(str(exc))

    def search_messages(self, subject="", from_address="", body="", limit: int = 10):
        """Retrieve up to ``limit`` recent messages matching the filters.

        Parameters are case-insensitive fragments. Results are returned as a list
        of dictionaries with ``subject``, ``from`` and ``body`` keys.
        """

        def _get_body(msg):
            if msg.is_multipart():
                for part in msg.walk():
                    if (
                        part.get_content_type() == "text/plain"
                        and not part.get_filename()
                    ):
                        charset = part.get_content_charset() or "utf-8"
                        return part.get_payload(decode=True).decode(
                            charset, errors="ignore"
                        )
                return ""
            charset = msg.get_content_charset() or "utf-8"
            return msg.get_payload(decode=True).decode(charset, errors="ignore")

        if self.protocol == self.IMAP:
            import imaplib
            import email

            conn = (
                imaplib.IMAP4_SSL(self.host, self.port)
                if self.use_ssl
                else imaplib.IMAP4(self.host, self.port)
            )
            conn.login(self.username, self.password)
            conn.select("INBOX")
            criteria = []
            if subject:
                criteria.extend(["SUBJECT", f'"{subject}"'])
            if from_address:
                criteria.extend(["FROM", f'"{from_address}"'])
            if body:
                criteria.extend(["TEXT", f'"{body}"'])
            if not criteria:
                criteria = ["ALL"]
            typ, data = conn.search(None, *criteria)
            ids = data[0].split()[-limit:]
            messages = []
            for mid in ids:
                typ, msg_data = conn.fetch(mid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                messages.append(
                    {
                        "subject": msg.get("Subject", ""),
                        "from": msg.get("From", ""),
                        "body": _get_body(msg),
                    }
                )
            conn.logout()
            return list(reversed(messages))

        import poplib
        import email

        conn = (
            poplib.POP3_SSL(self.host, self.port)
            if self.use_ssl
            else poplib.POP3(self.host, self.port)
        )
        conn.user(self.username)
        conn.pass_(self.password)
        count = len(conn.list()[1])
        messages = []
        for i in range(count, 0, -1):
            resp, lines, octets = conn.retr(i)
            msg = email.message_from_bytes(b"\n".join(lines))
            subj = msg.get("Subject", "")
            frm = msg.get("From", "")
            body_text = _get_body(msg)
            if subject and subject.lower() not in subj.lower():
                continue
            if from_address and from_address.lower() not in frm.lower():
                continue
            if body and body.lower() not in body_text.lower():
                continue
            messages.append({"subject": subj, "from": frm, "body": body_text})
            if len(messages) >= limit:
                break
        conn.quit()
        return messages

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.username}@{self.host}"


class EmailCollector(Entity):
    """Search an inbox for matching messages and extract data via sigils."""

    inbox = models.ForeignKey(
        "EmailInbox",
        related_name="collectors",
        on_delete=models.CASCADE,
    )
    subject = models.CharField(max_length=255, blank=True)
    sender = models.CharField(max_length=255, blank=True)
    body = models.CharField(max_length=255, blank=True)
    fragment = models.CharField(
        max_length=255,
        blank=True,
        help_text="Pattern with [sigils] to extract values from the body.",
    )

    def _parse_sigils(self, text: str) -> dict[str, str]:
        """Extract values from ``text`` according to ``fragment`` sigils."""
        if not self.fragment:
            return {}
        import re

        parts = re.split(r"\[([^\]]+)\]", self.fragment)
        pattern = ""
        for idx, part in enumerate(parts):
            if idx % 2 == 0:
                pattern += re.escape(part)
            else:
                pattern += f"(?P<{part}>.+)"
        match = re.search(pattern, text)
        if not match:
            return {}
        return {k: v.strip() for k, v in match.groupdict().items()}

    def collect(self, limit: int = 10) -> None:
        """Poll the inbox and store new artifacts until an existing one is found."""
        from .models import EmailArtifact

        messages = self.inbox.search_messages(
            subject=self.subject,
            from_address=self.sender,
            body=self.body,
            limit=limit,
        )
        for msg in messages:
            fp = EmailArtifact.fingerprint_for(
                msg.get("subject", ""), msg.get("from", ""), msg.get("body", "")
            )
            if EmailArtifact.objects.filter(collector=self, fingerprint=fp).exists():
                break
            EmailArtifact.objects.create(
                collector=self,
                subject=msg.get("subject", ""),
                sender=msg.get("from", ""),
                body=msg.get("body", ""),
                sigils=self._parse_sigils(msg.get("body", "")),
                fingerprint=fp,
            )

    class Meta:
        verbose_name = _("Email Collector")
        verbose_name_plural = _("Email Collectors")


class EmailArtifact(Entity):
    """Store messages discovered by :class:`EmailCollector`."""

    collector = models.ForeignKey(
        EmailCollector, related_name="artifacts", on_delete=models.CASCADE
    )
    subject = models.CharField(max_length=255)
    sender = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    sigils = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=32)

    @staticmethod
    def fingerprint_for(subject: str, sender: str, body: str) -> str:
        import hashlib

        data = (subject or "") + (sender or "") + (body or "")
        return hashlib.md5(data.encode("utf-8")).hexdigest()

    class Meta:
        unique_together = ("collector", "fingerprint")
        verbose_name = "Email Artifact"
        verbose_name_plural = "Email Artifacts"
        ordering = ["-id"]


class Reference(Entity):
    """Store a piece of reference content which can be text or an image."""

    TEXT = "text"
    IMAGE = "image"
    CONTENT_TYPE_CHOICES = [
        (TEXT, "Text"),
        (IMAGE, "Image"),
    ]

    content_type = models.CharField(
        max_length=5, choices=CONTENT_TYPE_CHOICES, default=TEXT
    )
    alt_text = models.CharField("Title / Alt Text", max_length=500)
    value = models.TextField(blank=True)
    file = models.FileField(upload_to="refs/", blank=True)
    image = models.ImageField(upload_to="refs/qr/", blank=True)
    uses = models.PositiveIntegerField(default=0)
    method = models.CharField(max_length=50, default="qr")
    include_in_footer = models.BooleanField(
        default=False, verbose_name="Include in Footer"
    )
    FOOTER_PUBLIC = "public"
    FOOTER_PRIVATE = "private"
    FOOTER_STAFF = "staff"
    FOOTER_VISIBILITY_CHOICES = [
        (FOOTER_PUBLIC, "Public"),
        (FOOTER_PRIVATE, "Private"),
        (FOOTER_STAFF, "Staff"),
    ]
    footer_visibility = models.CharField(
        max_length=7,
        choices=FOOTER_VISIBILITY_CHOICES,
        default=FOOTER_PUBLIC,
        verbose_name="Footer visibility",
    )
    transaction_uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=True,
        db_index=True,
        verbose_name="transaction UUID",
    )
    created = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="references",
        null=True,
        blank=True,
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.get(pk=self.pk)
            if original.transaction_uuid != self.transaction_uuid:
                raise ValidationError(
                    {"transaction_uuid": "Cannot modify transaction UUID"}
                )
        if not self.image and self.value:
            qr = qrcode.QRCode(box_size=10, border=4)
            qr.add_data(self.value)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            filename = hashlib.sha256(self.value.encode()).hexdigest()[:16] + ".png"
            self.image.save(filename, ContentFile(buffer.getvalue()), save=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.alt_text


class RFID(Entity):
    """RFID tag that may be assigned to one account."""

    label_id = models.AutoField(primary_key=True, db_column="label_id")
    rfid = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="RFID",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]+$",
                message="RFID must be hexadecimal digits",
            )
        ],
    )
    key_a = models.CharField(
        max_length=12,
        default="FFFFFFFFFFFF",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{12}$",
                message="Key must be 12 hexadecimal digits",
            )
        ],
        verbose_name="Key A",
    )
    key_b = models.CharField(
        max_length=12,
        default="FFFFFFFFFFFF",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{12}$",
                message="Key must be 12 hexadecimal digits",
            )
        ],
        verbose_name="Key B",
    )
    data = models.JSONField(
        default=list,
        blank=True,
        help_text="Sector and block data",
    )
    key_a_verified = models.BooleanField(default=False)
    key_b_verified = models.BooleanField(default=False)
    allowed = models.BooleanField(default=True)
    BLACK = "B"
    WHITE = "W"
    BLUE = "U"
    RED = "R"
    GREEN = "G"
    COLOR_CHOICES = [
        (BLACK, "Black"),
        (WHITE, "White"),
        (BLUE, "Blue"),
        (RED, "Red"),
        (GREEN, "Green"),
    ]
    color = models.CharField(
        max_length=1,
        choices=COLOR_CHOICES,
        default=BLACK,
    )
    CLASSIC = "CLASSIC"
    NTAG215 = "NTAG215"
    KIND_CHOICES = [
        (CLASSIC, "MIFARE Classic"),
        (NTAG215, "NTAG215"),
    ]
    kind = models.CharField(
        max_length=8,
        choices=KIND_CHOICES,
        default=CLASSIC,
    )
    reference = models.ForeignKey(
        "Reference",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rfids",
        help_text="Optional reference for this RFID.",
    )
    released = models.BooleanField(default=False)
    added_on = models.DateTimeField(auto_now_add=True)
    last_seen_on = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values("key_a", "key_b").first()
            if old:
                if self.key_a and old["key_a"] != self.key_a.upper():
                    self.key_a_verified = False
                if self.key_b and old["key_b"] != self.key_b.upper():
                    self.key_b_verified = False
        if self.rfid:
            self.rfid = self.rfid.upper()
        if self.key_a:
            self.key_a = self.key_a.upper()
        if self.key_b:
            self.key_b = self.key_b.upper()
        if self.kind:
            self.kind = self.kind.upper()
        super().save(*args, **kwargs)
        if not self.allowed:
            self.energy_accounts.clear()

    def __str__(self):  # pragma: no cover - simple representation
        return str(self.label_id)

    @staticmethod
    def get_account_by_rfid(value):
        """Return the energy account associated with an RFID code if it exists."""
        try:
            EnergyAccount = apps.get_model("core", "EnergyAccount")
        except LookupError:  # pragma: no cover - energy accounts app optional
            return None
        return EnergyAccount.objects.filter(
            rfids__rfid=value.upper(), rfids__allowed=True
        ).first()

    class Meta:
        verbose_name = "RFID"
        verbose_name_plural = "RFIDs"
        db_table = "core_rfid"


class EnergyAccount(Entity):
    """Track kW energy credits for a user."""

    name = models.CharField(max_length=100, unique=True)
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="energy_account",
        null=True,
        blank=True,
    )
    rfids = models.ManyToManyField(
        "RFID",
        blank=True,
        related_name="energy_accounts",
        db_table="core_account_rfids",
        verbose_name="RFIDs",
    )
    service_account = models.BooleanField(
        default=False,
        help_text="Allow transactions even when the balance is zero or negative",
    )

    def can_authorize(self) -> bool:
        """Return True if this account should be authorized for charging."""
        return self.service_account or self.balance_kw > 0

    @property
    def credits_kw(self):
        """Total kW energy credits added to the energy account."""
        from django.db.models import Sum
        from decimal import Decimal

        total = self.credits.aggregate(total=Sum("amount_kw"))["total"]
        return total if total is not None else Decimal("0")

    @property
    def total_kw_spent(self):
        """Total kW consumed across all transactions."""
        from django.db.models import F, Sum, ExpressionWrapper, FloatField
        from decimal import Decimal

        expr = ExpressionWrapper(
            F("meter_stop") - F("meter_start"), output_field=FloatField()
        )
        total = self.transactions.filter(
            meter_start__isnull=False, meter_stop__isnull=False
        ).aggregate(total=Sum(expr))["total"]
        if total is None:
            return Decimal("0")
        return Decimal(str(total))

    @property
    def balance_kw(self):
        """Remaining kW available for the energy account."""
        return self.credits_kw - self.total_kw_spent

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.upper()
        super().save(*args, **kwargs)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = "Energy Account"
        verbose_name_plural = "Energy Accounts"
        db_table = "core_account"


class EnergyCredit(Entity):
    """Energy credits added to an energy account."""

    account = models.ForeignKey(
        EnergyAccount, on_delete=models.CASCADE, related_name="credits"
    )
    amount_kw = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="Energy (kW)"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_entries",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        user = (
            self.account.user
            if self.account.user
            else f"Energy Account {self.account_id}"
        )
        return f"{self.amount_kw} kW for {user}"

    class Meta:
        verbose_name = "Energy Credit"
        verbose_name_plural = "Energy Credits"
        db_table = "core_credit"


class EnergyReport(Entity):
    """Snapshot of energy usage over a period."""

    start_date = models.DateField()
    end_date = models.DateField()
    created_on = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Energy Report"
        verbose_name_plural = "Energy Reports"
        db_table = "core_energy_report"
        ordering = ["-created_on"]

    @classmethod
    def generate(cls, start_date, end_date):
        rows = cls.build_rows(start_date, end_date)
        return cls.objects.create(
            start_date=start_date, end_date=end_date, data={"rows": rows}
        )

    @staticmethod
    def build_rows(start_date=None, end_date=None):
        from collections import defaultdict
        from ocpp.models import Transaction

        qs = Transaction.objects.exclude(rfid="")
        if start_date:
            from datetime import datetime, time, timedelta, timezone as pytimezone

            start_dt = datetime.combine(start_date, time.min, tzinfo=pytimezone.utc)
            qs = qs.filter(start_time__gte=start_dt)
        if end_date:
            from datetime import datetime, time, timedelta, timezone as pytimezone

            end_dt = datetime.combine(
                end_date + timedelta(days=1), time.min, tzinfo=pytimezone.utc
            )
            qs = qs.filter(start_time__lt=end_dt)
        data = defaultdict(lambda: {"kw": 0.0, "count": 0})
        for tx in qs:
            data[tx.rfid]["kw"] += tx.kw
            data[tx.rfid]["count"] += 1
        rows = []
        for rfid_uid, stats in sorted(data.items()):
            tag = RFID.objects.filter(rfid=rfid_uid).first()
            if tag:
                account = tag.energy_accounts.first()
                if account:
                    subject = account.name
                else:
                    subject = str(tag.label_id)
            else:
                subject = rfid_uid
            rows.append(
                {"subject": subject, "kw": stats["kw"], "count": stats["count"]}
            )
        return rows


class Brand(Entity):
    """Vehicle manufacturer or brand."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = _("EV Brand")
        verbose_name_plural = _("EV Brands")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @classmethod
    def from_vin(cls, vin: str) -> "Brand | None":
        """Return the brand matching the VIN's WMI prefix."""
        if not vin:
            return None
        prefix = vin[:3].upper()
        return cls.objects.filter(wmi_codes__code=prefix).first()


class WMICode(Entity):
    """World Manufacturer Identifier code for a brand."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="wmi_codes")
    code = models.CharField(max_length=3, unique=True)

    class Meta:
        verbose_name = _("WMI Code")
        verbose_name_plural = _("WMI Codes")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.code


class EVModel(Entity):
    """Specific electric vehicle model for a brand."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="ev_models")
    name = models.CharField(max_length=100)
    battery_capacity_kwh = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Battery Capacity (kWh)",
    )
    ac_110v_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="110V AC (kW)",
    )
    ac_220v_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="220V AC (kW)",
    )
    dc_60_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="60kW DC (kW)",
    )
    dc_100_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="100kW DC (kW)",
    )

    class Meta:
        unique_together = ("brand", "name")
        verbose_name = _("EV Model")
        verbose_name_plural = _("EV Models")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.brand} {self.name}" if self.brand else self.name


class ElectricVehicle(Entity):
    """Electric vehicle associated with an Energy Account."""

    account = models.ForeignKey(
        EnergyAccount, on_delete=models.CASCADE, related_name="vehicles"
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
    )
    model = models.ForeignKey(
        EVModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
    )
    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")
    license_plate = models.CharField(_("License Plate"), max_length=20, blank=True)

    def save(self, *args, **kwargs):
        if self.model and not self.brand:
            self.brand = self.model.brand
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        brand_name = self.brand.name if self.brand else ""
        model_name = self.model.name if self.model else ""
        parts = " ".join(p for p in [brand_name, model_name] if p)
        return f"{parts} ({self.vin})" if parts else self.vin

    class Meta:
        verbose_name = _("Electric Vehicle")
        verbose_name_plural = _("Electric Vehicles")


class Product(Entity):
    """A product that users can subscribe to."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    renewal_period = models.PositiveIntegerField(help_text="Renewal period in days")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class Subscription(Entity):
    """An energy account's subscription to a product."""

    account = models.ForeignKey(EnergyAccount, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    start_date = models.DateField(auto_now_add=True)
    next_renewal = models.DateField(blank=True)

    def save(self, *args, **kwargs):
        if not self.next_renewal:
            self.next_renewal = self.start_date + timedelta(
                days=self.product.renewal_period
            )
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.account.user} -> {self.product}"


class AdminHistory(Entity):
    """Record of recently visited admin changelists for a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_history"
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    url = models.TextField()
    visited_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-visited_at"]
        unique_together = ("user", "url")
        verbose_name = "Admin History"
        verbose_name_plural = "Admin Histories"

    @property
    def admin_label(self) -> str:  # pragma: no cover - simple representation
        model = self.content_type.model_class()
        return model._meta.verbose_name_plural if model else self.content_type.name


class ReleaseManager(Entity):
    """Store credentials for publishing packages."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="release_manager",
    )
    pypi_username = SigilShortAutoField("PyPI username", max_length=100, blank=True)
    pypi_token = SigilShortAutoField("PyPI token", max_length=200, blank=True)
    github_token = SigilShortAutoField(
        max_length=200,
        blank=True,
        help_text=(
            "Personal access token for GitHub operations. "
            "Used before the GITHUB_TOKEN environment variable."
        ),
    )
    pypi_password = SigilShortAutoField("PyPI password", max_length=200, blank=True)
    pypi_url = SigilShortAutoField("PyPI URL", max_length=200, blank=True)

    class Meta:
        verbose_name = "Release Manager"
        verbose_name_plural = "Release Managers"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    @property
    def name(self) -> str:  # pragma: no cover - simple proxy
        return self.user.get_username()

    def to_credentials(self) -> Credentials | None:
        """Return credentials for this release manager."""
        if self.pypi_token:
            return Credentials(token=self.pypi_token)
        if self.pypi_username and self.pypi_password:
            return Credentials(username=self.pypi_username, password=self.pypi_password)
        return None


class Package(Entity):
    """Package details shared across releases."""

    name = models.CharField(max_length=100, default=DEFAULT_PACKAGE.name, unique=True)
    description = models.CharField(max_length=255, default=DEFAULT_PACKAGE.description)
    author = models.CharField(max_length=100, default=DEFAULT_PACKAGE.author)
    email = models.EmailField(default=DEFAULT_PACKAGE.email)
    python_requires = models.CharField(
        max_length=20, default=DEFAULT_PACKAGE.python_requires
    )
    license = models.CharField(max_length=100, default=DEFAULT_PACKAGE.license)
    repository_url = models.URLField(default=DEFAULT_PACKAGE.repository_url)
    homepage_url = models.URLField(default=DEFAULT_PACKAGE.homepage_url)
    release_manager = models.ForeignKey(
        ReleaseManager, on_delete=models.SET_NULL, null=True, blank=True
    )
    is_active = models.BooleanField(
        default=False,
        help_text="Designates the active package for version comparisons",
    )

    class Meta:
        verbose_name = "Package"
        verbose_name_plural = "Packages"
        constraints = [
            models.UniqueConstraint(
                fields=("is_active",),
                condition=models.Q(is_active=True),
                name="unique_active_package",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def save(self, *args, **kwargs):
        if self.is_active:
            type(self).objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def to_package(self) -> ReleasePackage:
        """Return a :class:`ReleasePackage` instance from package data."""
        return ReleasePackage(
            name=self.name,
            description=self.description,
            author=self.author,
            email=self.email,
            python_requires=self.python_requires,
            license=self.license,
            repository_url=self.repository_url,
            homepage_url=self.homepage_url,
        )


class PackageRelease(Entity):
    """Store metadata for a specific package version."""

    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name="releases"
    )
    release_manager = models.ForeignKey(
        ReleaseManager, on_delete=models.SET_NULL, null=True, blank=True
    )
    version = models.CharField(max_length=20, default="0.0.0")
    revision = models.CharField(
        max_length=40, blank=True, default=revision_utils.get_revision, editable=False
    )
    pypi_url = models.URLField("PyPI URL", blank=True, editable=False)

    class Meta:
        verbose_name = "Package Release"
        verbose_name_plural = "Package Releases"
        get_latest_by = "version"
        constraints = [
            models.UniqueConstraint(
                fields=("package", "version"), name="unique_package_version"
            )
        ]

    @classmethod
    def dump_fixture(cls) -> None:
        base = Path("core/fixtures")
        base.mkdir(parents=True, exist_ok=True)
        for old in base.glob("releases__*.json"):
            old.unlink()
        for release in cls.objects.all():
            name = f"releases__packagerelease_{release.version.replace('.', '_')}.json"
            path = base / name
            data = serializers.serialize("json", [release])
            path.write_text(data)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.package.name} {self.version}"

    def to_package(self) -> ReleasePackage:
        """Return a :class:`ReleasePackage` built from the package."""
        return self.package.to_package()

    def to_credentials(self) -> Credentials | None:
        """Return :class:`Credentials` from the associated release manager."""
        manager = self.release_manager or self.package.release_manager
        if manager:
            return manager.to_credentials()
        return None

    def get_github_token(self) -> str | None:
        """Return GitHub token from the associated release manager or environment."""
        manager = self.release_manager or self.package.release_manager
        if manager and manager.github_token:
            return manager.github_token
        return os.environ.get("GITHUB_TOKEN")

    @property
    def migration_number(self) -> int:
        """Return the migration number derived from the version bits."""
        from packaging.version import Version

        v = Version(self.version)
        return (v.major << 2) | (v.minor << 1) | v.micro

    @staticmethod
    def version_from_migration(number: int) -> str:
        """Return version string encoded by ``number``."""
        major = (number >> 2) & 0x3FFFFF
        minor = (number >> 1) & 0x1
        patch = number & 0x1
        return f"{major}.{minor}.{patch}"

    @property
    def is_published(self) -> bool:
        """Return ``True`` if this release has been published."""
        return bool(self.pypi_url)

    @property
    def is_current(self) -> bool:
        """Return ``True`` when this release's version matches the VERSION file
        and its package is active."""
        version_path = Path("VERSION")
        if not version_path.exists():
            return False
        current_version = version_path.read_text().strip()
        return current_version == self.version and self.package.is_active

    @classmethod
    def latest(cls):
        """Return the latest release by version."""
        from packaging.version import Version

        releases = list(cls.objects.all())
        if not releases:
            return None
        return max(releases, key=lambda r: Version(r.version))

    def build(self, **kwargs) -> None:
        """Wrapper around :func:`core.release.build` for convenience."""
        from . import release as release_utils
        from utils import revision as revision_utils

        release_utils.build(
            package=self.to_package(),
            version=self.version,
            creds=self.to_credentials(),
            **kwargs,
        )
        self.revision = revision_utils.get_revision()
        self.save(update_fields=["revision"])
        PackageRelease.dump_fixture()
        if kwargs.get("git"):
            from glob import glob

            paths = sorted(glob("core/fixtures/releases__*.json"))
            diff = subprocess.run(
                ["git", "status", "--porcelain", *paths],
                capture_output=True,
                text=True,
            )
            if diff.stdout.strip():
                release_utils._run(["git", "add", *paths])
                release_utils._run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"chore: update release fixture for v{self.version}",
                    ]
                )
                release_utils._run(["git", "push"])

    @property
    def revision_short(self) -> str:
        return self.revision[-6:] if self.revision else ""


# Ensure each RFID can only be linked to one energy account
@receiver(m2m_changed, sender=EnergyAccount.rfids.through)
def _rfid_unique_energy_account(
    sender, instance, action, reverse, model, pk_set, **kwargs
):
    """Prevent associating an RFID with more than one energy account."""
    if action == "pre_add":
        if reverse:  # adding energy accounts to an RFID
            if instance.energy_accounts.exclude(pk__in=pk_set).exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one energy account."
                )
        else:  # adding RFIDs to an energy account
            conflict = model.objects.filter(
                pk__in=pk_set, energy_accounts__isnull=False
            ).exclude(energy_accounts=instance)
            if conflict.exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one energy account."
                )


def hash_key(key: str) -> str:
    """Return a SHA-256 hash for ``key``."""

    return hashlib.sha256(key.encode()).hexdigest()


class ChatProfile(Entity):
    """Stores a hashed user key used by the assistant for authentication.

    The plain-text ``user_key`` is generated server-side and shown only once.
    Users must supply this key in the ``Authorization: Bearer <user_key>``
    header when requesting protected endpoints. Only the hash is stored.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_profile"
    )
    user_key_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "workgroup_chatprofile"
        verbose_name = "Chat Profile"
        verbose_name_plural = "Chat Profiles"

    @classmethod
    def issue_key(cls, user) -> tuple["ChatProfile", str]:
        """Create or update a profile and return it with a new plain key."""

        key = secrets.token_hex(32)
        key_hash = hash_key(key)
        profile, _ = cls.objects.update_or_create(
            user=user,
            defaults={
                "user_key_hash": key_hash,
                "last_used_at": None,
                "is_active": True,
            },
        )
        return profile, key

    def touch(self) -> None:
        """Record that the key was used."""

        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"ChatProfile for {self.user}"


class NewsArticle(Entity):
    """Public-facing news items for system updates."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    content = models.TextField()
    published = models.DateField(default=timezone.now)
    version = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["-published"]
        verbose_name = "News Article"
        verbose_name_plural = "News Articles"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def changelog_html(self) -> str:
        if not self.version:
            return ""
        changelog_path = Path(settings.BASE_DIR) / "CHANGELOG.rst"
        try:
            text = changelog_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        pattern = rf"(?ms)^{re.escape(self.version)}.*?\n-+\n(.*?)(?=\n(?:\d+\.\d+|Unreleased)\b.*?\n-+|\Z)"
        match = re.search(pattern, text)
        if not match:
            return ""
        section = match.group(1).strip()
        if not section:
            return ""
        html = publish_parts(section, writer_name="html5")["html_body"]
        return mark_safe(html)


def validate_relative_url(value: str) -> None:
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        raise ValidationError("URL must be relative")


class Todo(Entity):
    """Tasks requested for the Release Manager."""

    description = models.CharField(max_length=255)
    url = models.CharField(
        max_length=200, blank=True, default="", validators=[validate_relative_url]
    )

    class Meta:
        verbose_name = "TODO"
        verbose_name_plural = "TODOs"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.description
