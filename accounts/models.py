from django.contrib.auth.models import AbstractUser
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
from urllib.parse import urljoin
from django.contrib.contenttypes.models import ContentType


class Address(models.Model):
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
        (name, name)
        for name in COAHUILA_MUNICIPALITIES + NUEVO_LEON_MUNICIPALITIES
    ]

    street = models.CharField(max_length=255)
    number = models.CharField(max_length=20)
    municipality = models.CharField(max_length=100, choices=MUNICIPALITY_CHOICES)
    state = models.CharField(max_length=2, choices=State.choices)
    postal_code = models.CharField(max_length=10)

    def clean(self):
        from django.core.exceptions import ValidationError

        allowed = self.MUNICIPALITIES_BY_STATE.get(self.state, [])
        if self.municipality not in allowed:
            raise ValidationError(
                {"municipality": _("Invalid municipality for the selected state")}
            )

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.street} {self.number}, {self.municipality}, {self.state}"


class User(AbstractUser):
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


class UserProxy(User):
    """Proxy model to display users under the auth app in admin."""

    class Meta:
        proxy = True
        app_label = "auth"
        verbose_name = User._meta.verbose_name
        verbose_name_plural = User._meta.verbose_name_plural


class RFID(models.Model):
    """RFID tag that may be assigned to one account."""

    label_id = models.AutoField(primary_key=True, db_column="label_id")
    rfid = models.CharField(
        max_length=8,
        unique=True,
        verbose_name="RFID",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{8}$",
                message="RFID must be 8 hexadecimal digits",
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
    allowed = models.BooleanField(default=True)
    added_on = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
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
        Account = apps.get_model("accounts", "Account")
        return (
            Account.objects.filter(
                rfids__rfid=value.upper(), rfids__allowed=True
            )
            .first()
        )

    class Meta:
        verbose_name = "RFID"
        verbose_name_plural = "RFIDs"
        db_table = "accounts_rfid"


class RFIDSource(models.Model):
    """Endpoint configuration for syncing RFIDs."""

    name = models.CharField(max_length=100, unique=True)
    endpoint = models.SlugField(
        max_length=50,
        help_text="Slug for the RFID batch endpoint; '/api/rfid/' is added automatically",
    )
    is_source = models.BooleanField(default=False)
    is_target = models.BooleanField(default=False)

    class Meta:
        verbose_name = "RFID source"
        verbose_name_plural = "RFID sources"

    def set_source(self, value: bool = True) -> None:
        """Idempotently mark this endpoint as a source for RFIDs."""
        if self.is_source != value:
            self.is_source = value
            self.save(update_fields=["is_source"])

    def set_target(self, value: bool = True) -> None:
        """Idempotently mark this endpoint as a target for RFIDs."""
        if self.is_target != value:
            self.is_target = value
            self.save(update_fields=["is_target"])

    def _build_url(self, base_url: str) -> str:
        """Construct the full RFID endpoint URL from a base URL."""

        return urljoin(base_url.rstrip("/") + "/", f"api/rfid/{self.endpoint.strip('/')}/")

    def test_fetch(self, base_url: str):
        """Fetch RFIDs from the endpoint without persisting them."""
        import requests

        url = self._build_url(base_url)
        resp = requests.get(url, params={"test": "true"})
        resp.raise_for_status()
        return resp.json()

    def test_serve(self, rfids=None, base_url: str = None):
        """Send RFIDs to the endpoint without altering remote data."""
        import requests

        url = self._build_url(base_url)
        payload = {"test": True}
        if rfids is not None:
            payload["rfids"] = rfids
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


class Account(models.Model):
    """Track kW credits for a user."""

    name = models.CharField(max_length=100, unique=True)
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="account",
        null=True,
        blank=True,
    )
    rfids = models.ManyToManyField(
        "RFID", blank=True, related_name="accounts"
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
        total = (
            self.transactions.filter(
                meter_start__isnull=False, meter_stop__isnull=False
            ).aggregate(total=Sum(expr))["total"]
        )
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


class Credit(models.Model):
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


class Brand(models.Model):
    """Vehicle manufacturer or brand."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = _("EV Brand")
        verbose_name_plural = _("EV Brands")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class WMICode(models.Model):
    """World Manufacturer Identifier code for a brand."""

    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, related_name="wmi_codes"
    )
    code = models.CharField(max_length=3, unique=True)

    class Meta:
        verbose_name = _("WMI code")
        verbose_name_plural = _("WMI codes")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.code


class EVModel(models.Model):
    """Specific electric vehicle model for a brand."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="ev_models")
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ("brand", "name")
        verbose_name = _("EV Model")
        verbose_name_plural = _("EV Models")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.brand} {self.name}" if self.brand else self.name


class Vehicle(models.Model):
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


class Product(models.Model):
    """A product that users can subscribe to."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    renewal_period = models.PositiveIntegerField(help_text="Renewal period in days")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class Subscription(models.Model):
    """An account's subscription to a product."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    start_date = models.DateField(auto_now_add=True)
    next_renewal = models.DateField(blank=True)

    def save(self, *args, **kwargs):
        if not self.next_renewal:
            self.next_renewal = self.start_date + timedelta(days=self.product.renewal_period)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.account.user} -> {self.product}"


class AdminHistory(models.Model):
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


# Ensure each RFID can only be linked to one account
@receiver(m2m_changed, sender=Account.rfids.through)
def _rfid_unique_account(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Prevent associating an RFID with more than one account."""
    if action == "pre_add":
        if reverse:  # adding accounts to an RFID
            if instance.accounts.exclude(pk__in=pk_set).exists():
                raise ValidationError("RFID tags may only be assigned to one account.")
        else:  # adding RFIDs to an account
            conflict = model.objects.filter(pk__in=pk_set, accounts__isnull=False).exclude(
                accounts=instance
            )
            if conflict.exists():
                raise ValidationError("RFID tags may only be assigned to one account.")
