from django.test import TestCase

from apps.counters.dashboard_rules import (
    _load_ocpp_rule_models,
    evaluate_cp_configuration_rules,
    evaluate_cp_firmware_rules,
    evaluate_evcs_heartbeat_rules,
)
from apps.ocpp.models import Charger, ChargerConfiguration, CPFirmware


class OcppDashboardRuleTests(TestCase):
    def test_supported_rules_load_without_retired_simulator_model(self):
        self.assertEqual(
            _load_ocpp_rule_models(),
            (Charger, ChargerConfiguration, CPFirmware),
        )

        for evaluate in (
            evaluate_cp_configuration_rules,
            evaluate_cp_firmware_rules,
            evaluate_evcs_heartbeat_rules,
        ):
            with self.subTest(rule=evaluate.__name__):
                result = evaluate()
                self.assertTrue(result["success"])
                self.assertNotIn("import err", result["message"])
