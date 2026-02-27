"""Forms used by Evergo admin and public tools."""

from django import forms
from django.utils import timezone

from .models import EvergoUser


class EvergoLoadCustomersForm(forms.Form):
    """Collect profile and free-form SO/name input for customer sync."""

    profile = forms.ModelChoiceField(
        queryset=EvergoUser.objects.all().order_by("evergo_email", "id"),
        help_text="Profile used to authenticate against Evergo.",
    )
    raw_queries = forms.CharField(
        label="SO numbers and/or customer names",
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


class EvergoOrderTrackingForm(forms.Form):
    """Collect phase-one data for the public Evergo order tracking flow."""

    metraje_visita_tecnica = forms.IntegerField(min_value=0, label="Metraje visita técnica")
    programacion_cargador = forms.ChoiceField(choices=(("16A", "16A"), ("32A", "32A")))
    capacidad_itm_principal = forms.IntegerField(min_value=1, initial=60)
    fecha_visita = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    voltaje_fase_fase = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2, label="Fase-Fase")
    voltaje_fase_tierra = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2, label="Fase-Tierra")
    voltaje_fase_neutro = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2, label="Fase-Neutro")
    voltaje_neutro_tierra = forms.DecimalField(
        min_value=0,
        max_digits=8,
        decimal_places=2,
        label="Neutro-Tierra",
    )
    marca_cargador = forms.ChoiceField(choices=(), required=False)
    numero_serie = forms.CharField(max_length=128, label="Número de Serie")

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
            self.initial.setdefault("fecha_visita", timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
