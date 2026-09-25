import json
from pathlib import Path

import pytest

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


def test_every_frozen_contract_has_an_executable_handler_or_validator() -> None:
    entries = support_matrix()

    assert len(entries) == len(ALL_ACTIONS)
    assert not unimplemented_actions()


@pytest.mark.parametrize(
    ("filename", "identity", "inbound_factory", "outbound_validators"),
    [
        ("ocpp16.json", "official-surface-16", V16InboundActions, V16_VALIDATORS),
        ("ocpp201.json", "official-surface-201", V201InboundActions, V201_VALIDATORS),
    ],
)
def test_official_surface_matches_implementation_plus_known_gaps(
    filename: str,
    identity: str,
    inbound_factory,
    outbound_validators,
) -> None:
    manifest = _load(filename)
    selected = Charger(identity=identity)
    implemented = {
        "charge_point_to_csms": set(inbound_factory(selected)._handlers),
        "csms_to_charge_point": set(outbound_validators),
    }
    directions = manifest["directions"]
    known_missing = manifest["known_missing_in_arthexis"]

    for direction in ("charge_point_to_csms", "csms_to_charge_point"):
        official = set(directions[direction])
        actual = implemented[direction]
        expected_missing = set(known_missing[direction])

        assert official - actual == expected_missing
        assert not actual - official


@pytest.mark.parametrize("filename", ["ocpp16.json", "ocpp201.json"])
def test_all_charger_originated_official_actions_are_implemented(filename: str) -> None:
    manifest = _load(filename)

    assert manifest["known_missing_in_arthexis"]["charge_point_to_csms"] == []
