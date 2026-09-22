from django.test import TestCase

from apps.ocpp.domain.configuration import record_variable
from tests.apps.ocpp.builders import charger


class ChargerVariableTests(TestCase):
    def test_record_variable_persists_retained_configuration_state(self) -> None:
        variable = record_variable(
            charger=charger("charger-1"),
            component="EVSE",
            variable="AvailabilityState",
            attribute_type="Actual",
            value="Available",
            mutable=False,
        )

        self.assertEqual(variable.value, "Available")
