from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _


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
    """RFID tag assigned to a user and marked allowed or not."""

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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="rfids",
        on_delete=models.SET_NULL,
    )
    allowed = models.BooleanField(default=True)
    added_on = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.rfid:
            self.rfid = self.rfid.upper()
        if not self.allowed:
            self.user = None
        super().save(*args, **kwargs)

    def __str__(self):  # pragma: no cover - simple representation
        return self.rfid

    @staticmethod
    def get_user_by_rfid(value):
        """Return the user associated with an RFID code if it exists."""
        tag = (
            RFID.objects.filter(
                rfid=value.upper(), allowed=True, user__isnull=False
            )
            .select_related("user")
            .first()
        )
        return tag.user if tag else None

    class Meta:
        verbose_name = "RFID"
        verbose_name_plural = "RFIDs"
        app_label = "auth"
        db_table = "accounts_rfid"


class Account(models.Model):
    """Track kWh credits for a user."""

    user = models.OneToOneField(
        get_user_model(), on_delete=models.CASCADE, related_name="account"
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
        return f"Account for {self.user}"


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
        return f"{self.amount_kwh} kWh for {self.account.user}"


class Vehicle(models.Model):
    """Vehicle associated with an Account."""

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="vehicles"
    )
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        parts = " ".join(p for p in [self.brand, self.model] if p)
        return f"{parts} ({self.vin})" if parts else self.vin
