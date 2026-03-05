"""Form tests for Evergo public workflows."""

from django.utils import timezone

from apps.evergo.forms import EvergoOrderTrackingForm


def test_order_tracking_form_defaults_fecha_visita_to_today_at_ten_am():
    """Regression: fecha_visita should default to local current date at 10:00."""

    form = EvergoOrderTrackingForm(charger_brands=[])
    expected = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)

    assert form.initial["fecha_visita"] == expected.strftime("%Y-%m-%dT%H:%M")


def test_order_tracking_form_places_prueba_carga_before_marca_cargador():
    """Regression: prueba_carga should render before marca_cargador."""

    field_names = list(EvergoOrderTrackingForm(charger_brands=[]).fields.keys())

    assert field_names.index("prueba_carga") < field_names.index("marca_cargador")
