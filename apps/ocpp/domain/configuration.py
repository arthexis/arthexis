"""Configuration and variable persistence services."""

from apps.ocpp.models import Charger, ChargerVariable


def record_variable(
    *,
    charger: Charger,
    component: str,
    variable: str,
    attribute_type: str,
    value: str,
    mutable: bool,
) -> ChargerVariable:
    """Upsert one retained configuration or OCPP 2.0.1 variable value."""
    record, _ = ChargerVariable.objects.update_or_create(
        charger=charger,
        component=component,
        variable=variable,
        attribute_type=attribute_type,
        defaults={"value": value, "mutable": mutable},
    )
    return record
