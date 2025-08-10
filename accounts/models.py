from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.core.validators import RegexValidator
from django.apps import apps
from datetime import timedelta


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
    """RFID tag that may be assigned to one or more accounts."""

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
    allowed = models.BooleanField(default=True)
    added_on = models.DateTimeField(auto_now_add=True)
    is_seed_data = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.rfid:
            self.rfid = self.rfid.upper()
        super().save(*args, **kwargs)
        if not self.allowed:
            self.accounts.clear()

    def __str__(self):  # pragma: no cover - simple representation
        return self.rfid

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
    endpoint = models.URLField()
    is_source = models.BooleanField(default=False)
    is_target = models.BooleanField(default=False)

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

    def test_fetch(self):
        """Fetch RFIDs from the endpoint without persisting them."""
        import requests

        resp = requests.get(self.endpoint, params={"test": "true"})
        resp.raise_for_status()
        return resp.json()

    def test_serve(self, rfids=None):
        """Send RFIDs to the endpoint without altering remote data."""
        import requests

        payload = {"test": True}
        if rfids is not None:
            payload["rfids"] = rfids
        resp = requests.post(self.endpoint, json=payload)
        resp.raise_for_status()
        return resp.json()


class Account(models.Model):
    """Track kWh credits for a user."""

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
        return self.service_account or self.balance_kwh > 0

    @property
    def credits_kwh(self):
        """Total kWh credits added to the account."""
        from django.db.models import Sum
        from decimal import Decimal

        total = self.credits.aggregate(total=Sum("amount_kwh"))["total"]
        return total if total is not None else Decimal("0")

    @property
    def total_kwh_spent(self):
        """Total kWh consumed across all transactions."""
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
    def balance_kwh(self):
        """Remaining kWh available for the account."""
        return self.credits_kwh - self.total_kwh_spent

    def __str__(self):  # pragma: no cover - simple representation
        return str(self.user) if self.user else f"Account {self.pk}"


class Credit(models.Model):
    """Credits added to an account."""

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="credits"
    )
    amount_kwh = models.DecimalField(max_digits=10, decimal_places=2)
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
        return f"{self.amount_kwh} kWh for {user}"


class Brand(models.Model):
    """Vehicle manufacturer or brand."""

    name = models.CharField(max_length=100, unique=True)
    is_seed_data = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("EV Brand")
        verbose_name_plural = _("EV Brands")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class EVModel(models.Model):
    """Specific electric vehicle model for a brand."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="ev_models")
    name = models.CharField(max_length=100)
    is_seed_data = models.BooleanField(default=False)

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
