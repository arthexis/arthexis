import pytest

from apps.ocpp.domain.configuration import record_variable
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_record_variable_persists_retained_configuration_state() -> None:
    variable = record_variable(
        charger=charger("charger-1"),
        component="EVSE",
        variable="AvailabilityState",
        attribute_type="Actual",
        value="Available",
        mutable=False,
    )

    assert variable.value == "Available"
