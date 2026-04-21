"""Forms used by Evergo admin and public tools."""

import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.admin import OwnableAdminForm

from .models import EvergoUser


class EvergoLoadCustomersForm(forms.Form):
    """Collect profile and free-form SO/name input for customer sync."""

    max_queries = 100

    profile = forms.ModelChoiceField(
        queryset=EvergoUser.objects.all().order_by("evergo_email", "id"),
        help_text="Profile used to authenticate against Evergo.",
    )
    raw_queries = forms.CharField(
        label="SO numbers and/or customer names",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "style": "padding: 12px 14px; line-height: 1.5;",
            }
        ),
        help_text=(
            "Paste values separated by spaces, commas, semicolons, pipes, tabs, or new lines. "
            "SO patterns like J00830 are detected automatically."
        ),
    )
    next_view = forms.ChoiceField(
        label="Open next",
        choices=(
            ("customers", "Customers"),
            ("orders", "Orders"),
        ),
        initial="customers",
        required=False,
        help_text="Choose which admin list should open after the sync completes.",
    )

    def __init__(self, *args, request_user=None, **kwargs):
        """Optionally preselect an Evergo profile owned by the current request user."""
        super().__init__(*args, **kwargs)
        if request_user is None or not request_user.is_authenticated:
            return

        owned_profiles = EvergoUser.objects.filter(user=request_user).order_by("evergo_email", "id")
        self.fields["profile"].queryset = owned_profiles

        if self.is_bound:
            return

        owned_profile = owned_profiles.first()
        if owned_profile:
            self.fields["profile"].initial = owned_profile.pk

    def clean_raw_queries(self) -> str:
        """Validate and bound free-form lookup input for filtered mode."""
        raw_queries = (self.cleaned_data.get("raw_queries") or "").strip()
        if not raw_queries:
            return raw_queries

        tokens = [chunk for chunk in re.split(r"[,;|\s]+", raw_queries) if chunk]
        if len(tokens) > self.max_queries:
            raise ValidationError(
                f"Too many values in raw_queries. Submit at most {self.max_queries} values."
            )
        return raw_queries

    def clean(self):
        """Require lookup input only for filtered mode while allowing empty all-customers mode."""
        cleaned_data = super().clean()
        mode = (self.data.get("load_mode") or "filtered").strip().lower()
        raw_queries = (cleaned_data.get("raw_queries") or "").strip()

        if mode == "all":
            cleaned_data["raw_queries"] = ""
            return cleaned_data

        if not raw_queries:
            self.add_error("raw_queries", "Enter at least one SO number or customer name.")
        return cleaned_data


class EvergoContractorLoginWizardForm(forms.ModelForm):
    """Collect contractor ownership, credentials, and setup options for the admin wizard."""

    validate_credentials = forms.BooleanField(
        initial=True,
        required=False,
        label="Validate credentials now",
        help_text="Run an Evergo login immediately after saving the contractor.",
    )
    load_all_customers = forms.BooleanField(
        initial=False,
        required=False,
        label="Load all customers after validation",
        help_text="Optionally perform the initial full customer and order load for this contractor.",
    )
    order_numbers = forms.CharField(
        required=False,
        label="Optional order numbers",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "style": "padding: 8px 10px;",
            }
        ),
        help_text="When full load is disabled, enter one or more order numbers separated by spaces or commas.",
    )

    class Meta:
        model = EvergoUser
        fields = ("user", "evergo_email", "evergo_password")

    def __init__(self, *args, request_user=None, **kwargs):
        """Prefill first-time contractor setup defaults for a smoother signup flow."""
        super().__init__(*args, **kwargs)
        self.request_user = request_user
        self.fields["evergo_password"].widget = forms.PasswordInput()
        if self.instance.pk:
            self.fields["evergo_password"].required = False
        self.fields["user"].required = False
        self.fields["user"].widget = forms.HiddenInput()
        if (
            not self.is_bound
            and not self.instance.pk
            and getattr(request_user, "is_authenticated", False)
            and self.fields["user"].initial is None
        ):
            self.fields["user"].initial = request_user
        self.fields["evergo_email"].help_text = "Email used to sign in to the Evergo contractor portal."
        self.fields["evergo_password"].help_text = "Password used to sign in to the Evergo contractor portal."

    def clean(self):
        """Require a single owner and force validation before optional initial loading."""
        cleaned_data = super().clean()
        if self.instance.pk and not cleaned_data.get("evergo_password"):
            cleaned_data["evergo_password"] = self.instance.evergo_password

        if getattr(self.request_user, "is_authenticated", False):
            cleaned_data["user"] = self.request_user
            self.instance.group = None
            self.instance.avatar = None
        if cleaned_data.get("user") is None:
            raise ValidationError("Could not resolve the current user for this contractor.")
        if cleaned_data.get("load_all_customers") and not cleaned_data.get("validate_credentials"):
            self.add_error(
                "load_all_customers",
                "Enable credential validation before running the initial customer load.",
            )
        if cleaned_data.get("order_numbers") and not cleaned_data.get("validate_credentials"):
            self.add_error(
                "order_numbers",
                "Enable credential validation before loading specific order numbers.",
            )
        if cleaned_data.get("load_all_customers"):
            cleaned_data["order_numbers"] = ""
        return cleaned_data


class EvergoUserAdminForm(OwnableAdminForm):
    """Allow user/group/avatar ownership while defaulting new records to the acting user."""

    owner_field_names = ("user", "group", "avatar")
    owner_conflict_message = "Choose exactly one owner: user, security group, or avatar."
    owner_required_message = "Choose a user, security group, or avatar owner for this contractor."

    class Meta:
        model = EvergoUser
        fields = "__all__"

    def __init__(self, *args, request_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_user = request_user

    def normalize_owner_data(self, cleaned):
        owners = [cleaned.get(field_name) for field_name in self.owner_field_names]
        owner_count = sum(owner is not None for owner in owners)
        if (
            owner_count == 0
            and not self.instance.pk
            and getattr(self.request_user, "is_authenticated", False)
        ):
            cleaned["user"] = self.request_user
        return cleaned


class EvergoOrderTrackingForm(forms.Form):
    """Collect phase-one data for the public Evergo order tracking flow."""

    metraje_visita_tecnica = forms.IntegerField(min_value=0, label="Metraje visita técnica", required=False)
    programacion_cargador = forms.ChoiceField(choices=(("16A", "16A"), ("32A", "32A")), required=False)
    capacidad_itm_principal = forms.IntegerField(min_value=1, initial=60, required=False)
    fecha_visita = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        required=False,
    )
    voltaje_fase_fase = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2, label="Fase-Fase", required=False)
    voltaje_fase_tierra = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2, label="Fase-Tierra", required=False)
    voltaje_fase_neutro = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2, label="Fase-Neutro", required=False)
    voltaje_neutro_tierra = forms.DecimalField(
        min_value=0,
        max_digits=8,
        decimal_places=2,
        initial=0,
        label="Neutro-Tierra",
        required=False,
    )
    prueba_carga = forms.ChoiceField(
        choices=(
            ("Vehículo Eléctrico", "Vehículo Eléctrico"),
            ("Vehículo Hibrido", "Vehículo Hibrido"),
            ("Sin prueba", "Sin prueba"),
        ),
        initial="Sin prueba",
        label="Prueba de carga",
        required=False,
    )
    marca_cargador = forms.ChoiceField(choices=(), required=False)
    numero_serie = forms.CharField(max_length=128, label="Número de Serie", required=False)


    foto_tablero = forms.ImageField(required=False)
    foto_medidor = forms.ImageField(required=False)
    foto_tierra = forms.ImageField(required=False)
    foto_ruta_cableado = forms.ImageField(required=False)
    foto_ubicacion_cargador = forms.ImageField(required=False)
    foto_general = forms.ImageField(required=False)
    foto_voltaje_fase_fase = forms.ImageField(required=False)
    foto_voltaje_fase_tierra = forms.ImageField(required=False)
    foto_voltaje_fase_neutro = forms.ImageField(required=False)
    foto_voltaje_neutro_tierra = forms.ImageField(required=False)
    foto_hoja_visita = forms.ImageField(required=False)
    foto_interruptor_principal = forms.ImageField(required=False)

    foto_panoramica_estacion = forms.ImageField(required=False, label="Panorámica de la estación")
    foto_numero_serie_cargador = forms.ImageField(required=False, label="Foto número de serie cargador")
    foto_interruptor_instalado = forms.ImageField(required=False, label="Interruptor instalado")
    foto_conexion_cargador = forms.ImageField(required=False, label="Conexión a cargador")
    foto_preparacion_cfe = forms.ImageField(required=False, label="Preparación CFE")
    foto_hoja_reporte_instalacion = forms.ImageField(required=False, label="Hoja del reporte de instalación")

    tipo_visita = forms.CharField(initial="Presencial", required=False)
    requiere_instalacion = forms.CharField(initial="Si", required=False)
    tipo_inmueble = forms.CharField(initial="Casa", required=False)
    concentracion_medidores = forms.CharField(initial="Si", required=False)
    servicio = forms.CharField(initial="Paquete", required=False)
    obra_civil = forms.CharField(initial="No", required=False)
    kit_cfe = forms.CharField(initial="No", required=False)
    calibre_principal = forms.CharField(initial="8", required=False)
    garantia = forms.CharField(initial="12 Meses", required=False)

    def __init__(self, *args, charger_brands: list[str] | None = None, **kwargs):
        """Seed charger brand choices and sensible datetime defaults."""
        super().__init__(*args, **kwargs)
        brands = charger_brands or []
        self.fields["marca_cargador"].choices = [("", "Selecciona una marca")] + [
            (brand, brand) for brand in brands
        ]
        if not self.is_bound:
            default_visit_time = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
            self.initial.setdefault("fecha_visita", default_visit_time.strftime("%Y-%m-%dT%H:%M"))


class EvergoDashboardLookupForm(forms.Form):
    """Collect free-form SO/customer queries for dashboard table generation."""

    max_queries = 100

    raw_queries = forms.CharField(
        max_length=4000,
        label="SO numbers and/or customer names",
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": "GM01162, GM01163\nJuan Perez",
            }
        ),
        help_text=(
            "Use one or many values separated by commas, spaces, semicolons, tabs, or line breaks."
        ),
    )

    def clean_raw_queries(self) -> str:
        """Trim and bound lookup tokens to limit dashboard query work."""
        raw_queries = (self.cleaned_data.get("raw_queries") or "").strip()
        tokens = [chunk for chunk in re.split(r"[,;|\s]+", raw_queries) if chunk]
        if len(tokens) > self.max_queries:
            raise ValidationError(
                f"Too many values in raw_queries. Submit at most {self.max_queries} values."
            )
        return raw_queries
