from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
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
from django.contrib.contenttypes.models import ContentType
import hashlib
from io import BytesIO
from django.core.files.base import ContentFile
import qrcode

from .entity import Entity, EntityUserManager
from .release import Package, Credentials, DEFAULT_PACKAGE


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
    address = models.ForeignKey(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    has_charger = models.BooleanField(default=False)

    def __str__(self):
        return self.username


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
    created = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="references",
        null=True,
        blank=True,
    )

    def save(self, *args, **kwargs):
        if self.method == "qr" and self.value:
            qr = qrcode.QRCode(box_size=10, border=4)
            qr.add_data(self.value)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            filename = hashlib.sha256(self.value.encode()).hexdigest()[:16] + ".png"
            if self.image:
                self.image.delete(save=False)
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
        super().save(*args, **kwargs)
        if not self.allowed:
            self.accounts.clear()

    def __str__(self):  # pragma: no cover - simple representation
        return str(self.label_id)

    @staticmethod
    def get_account_by_rfid(value):
        """Return the account associated with an RFID code if it exists."""
        try:
            Account = apps.get_model("core", "Account")
        except LookupError:  # pragma: no cover - accounts app optional
            return None
        return Account.objects.filter(
            rfids__rfid=value.upper(), rfids__allowed=True
        ).first()

    class Meta:
        verbose_name = "RFID"
        verbose_name_plural = "RFIDs"
        db_table = "core_rfid"


class Account(Entity):
    """Track kW credits for a user."""

    name = models.CharField(max_length=100, unique=True)
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="account",
        null=True,
        blank=True,
    )
    rfids = models.ManyToManyField("RFID", blank=True, related_name="accounts")
    service_account = models.BooleanField(
        default=False,
        help_text="Allow transactions even when the balance is zero or negative",
    )

    def can_authorize(self) -> bool:
        """Return True if this account should be authorized for charging."""
        return self.service_account or self.balance_kw > 0

    @property
    def credits_kw(self):
        """Total kW credits added to the account."""
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
        """Remaining kW available for the account."""
        return self.credits_kw - self.total_kw_spent

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.upper()
        super().save(*args, **kwargs)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name


class Credit(Entity):
    """Credits added to an account."""

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="credits"
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
        user = self.account.user if self.account.user else f"Account {self.account_id}"
        return f"{self.amount_kw} kW for {user}"


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
        verbose_name = _("WMI code")
        verbose_name_plural = _("WMI codes")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.code


class EVModel(Entity):
    """Specific electric vehicle model for a brand."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="ev_models")
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ("brand", "name")
        verbose_name = _("EV Model")
        verbose_name_plural = _("EV Models")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.brand} {self.name}" if self.brand else self.name


class Vehicle(Entity):
    """Vehicle associated with an Account."""

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="vehicles"
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

    def save(self, *args, **kwargs):
        if self.model and not self.brand:
            self.brand = self.model.brand
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        brand_name = self.brand.name if self.brand else ""
        model_name = self.model.name if self.model else ""
        parts = " ".join(p for p in [brand_name, model_name] if p)
        return f"{parts} ({self.vin})" if parts else self.vin


class Product(Entity):
    """A product that users can subscribe to."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    renewal_period = models.PositiveIntegerField(help_text="Renewal period in days")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class Subscription(Entity):
    """An account's subscription to a product."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE)
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

    @property
    def admin_label(self) -> str:  # pragma: no cover - simple representation
        model = self.content_type.model_class()
        return model._meta.verbose_name_plural if model else self.content_type.name


class Message(Entity):
    """System message that can be sent to LCD or GUI."""

    subject = models.CharField(max_length=32, blank=True)
    body = models.CharField(max_length=32, blank=True)
    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.subject} {self.body}".strip()


class PackageRelease(Entity):
    """Store metadata and credentials for building a PyPI release."""

    name = models.CharField(max_length=100, default=DEFAULT_PACKAGE.name)
    description = models.CharField(max_length=255, default=DEFAULT_PACKAGE.description)
    author = models.CharField(max_length=100, default=DEFAULT_PACKAGE.author)
    email = models.EmailField(default=DEFAULT_PACKAGE.email)
    python_requires = models.CharField(max_length=20, default=DEFAULT_PACKAGE.python_requires)
    license = models.CharField(max_length=100, default=DEFAULT_PACKAGE.license)
    repository_url = models.URLField(default=DEFAULT_PACKAGE.repository_url)
    homepage_url = models.URLField(default=DEFAULT_PACKAGE.homepage_url)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)
    token = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Package Release"
        verbose_name_plural = "Package Releases"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def to_package(self) -> Package:
        """Return a :class:`Package` instance for this configuration."""
        return Package(
            name=self.name,
            description=self.description,
            author=self.author,
            email=self.email,
            python_requires=self.python_requires,
            license=self.license,
            repository_url=self.repository_url,
            homepage_url=self.homepage_url,
        )

    def to_credentials(self) -> Credentials | None:
        """Return :class:`Credentials` if any credential fields are set."""
        if self.token:
            return Credentials(token=self.token)
        if self.username and self.password:
            return Credentials(username=self.username, password=self.password)
        return None

    def build(self, **kwargs) -> None:
        """Wrapper around :func:`core.release.build` for convenience."""
        from . import release as release_utils

        release_utils.build(package=self.to_package(), creds=self.to_credentials(), **kwargs)

# Ensure each RFID can only be linked to one account
@receiver(m2m_changed, sender=Account.rfids.through)
def _rfid_unique_account(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Prevent associating an RFID with more than one account."""
    if action == "pre_add":
        if reverse:  # adding accounts to an RFID
            if instance.accounts.exclude(pk__in=pk_set).exists():
                raise ValidationError("RFID tags may only be assigned to one account.")
        else:  # adding RFIDs to an account
            conflict = model.objects.filter(
                pk__in=pk_set, accounts__isnull=False
            ).exclude(accounts=instance)
            if conflict.exists():
                raise ValidationError("RFID tags may only be assigned to one account.")
