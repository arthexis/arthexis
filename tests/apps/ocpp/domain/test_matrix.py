import json
from pathlib import Path

from django.test import SimpleTestCase

from apps.ocpp.domain.matrix import support_matrix, unimplemented_actions
from apps.ocpp.models import Charger
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.protocol.v16.inbound import InboundActions as V16InboundActions
from apps.ocpp.protocol.v16.outbound import VALIDATORS as V16_VALIDATORS
from apps.ocpp.protocol.v201.inbound import InboundActions as V201InboundActions
from apps.ocpp.protocol.v201.outbound import VALIDATORS as V201_VALIDATORS

SPEC_DIR = Path("tests/ocpp/spec")


def _load(name: str) -> dict[str, object]:
    return json.loads((SPEC_DIR / name).read_text(encoding="utf-8"))


class SupportMatrixTests(SimpleTestCase):
    def test_every_frozen_contract_has_an_executable_handler_or_validator(self) -> None:
        entries = support_matrix()

        self.assertEqual(len(entries), len(ALL_ACTIONS))
        self.assertFalse(unimplemented_actions())


class OfficialOcppSurfaceTests(SimpleTestCase):
    def test_official_ocpp_16_surface_matches_implementation_plus_known_gaps(self) -> None:
        manifest = _load("ocpp16.json")
        charger = Charger(identity="official-surface-16")

        implemented = {
            "charge_point_to_csms": set(V16InboundActions(charger)._handlers),
            "csms_to_charge_point": set(V16_VALIDATORS),
        }

        self._assert_surface(manifest, implemented)

    def test_official_ocpp_201_surface_matches_implementation_plus_known_gaps(self) -> None:
        manifest = _load("ocpp201.json")
        charger = Charger(identity="official-surface-201")

        implemented = {
            "charge_point_to_csms": set(V201InboundActions(charger)._handlers),
            "csms_to_charge_point": set(V201_VALIDATORS),
        }

        self._assert_surface(manifest, implemented)

    def _assert_surface(
        self,
        manifest: dict[str, object],
        implemented: dict[str, set[str]],
    ) -> None:
        directions = manifest["directions"]
        known_missing = manifest["known_missing_in_arthexis"]

        for direction in ("charge_point_to_csms", "csms_to_charge_point"):
            official = set(directions[direction])
            actual = implemented[direction]
            expected_missing = set(known_missing[direction])

            with self.subTest(protocol=manifest["protocol"], direction=direction):
                self.assertEqual(
                    official - actual,
                    expected_missing,
                    msg=(
                        f"Unexpected official-surface gap for {manifest['protocol']} "
                        f"{direction}: missing={sorted(official - actual)!r}; "
                        f"expected={sorted(expected_missing)!r}"
                    ),
                )
                self.assertFalse(
                    actual - official,
                    msg=(
                        f"Implementation exposes actions outside the official "
                        f"{manifest['protocol']} surface for {direction}: "
                        f"{sorted(actual - official)!r}"
                    ),
                )

    def test_all_charger_originated_official_actions_are_implemented(self) -> None:
        for filename in ("ocpp16.json", "ocpp201.json"):
            manifest = _load(filename)
            with self.subTest(protocol=manifest["protocol"]):
                self.assertEqual(
                    manifest["known_missing_in_arthexis"]["charge_point_to_csms"],
                    [],
                )
