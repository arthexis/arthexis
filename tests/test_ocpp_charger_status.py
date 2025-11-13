import pytest

from ocpp.models import Charger


@pytest.mark.parametrize("status", sorted(Charger.OPERATIVE_STATUSES))
def test_availability_state_from_status_operatives(status):
    assert Charger.availability_state_from_status(status) == "Operative"


@pytest.mark.parametrize("status", sorted(Charger.INOPERATIVE_STATUSES))
def test_availability_state_from_status_inoperatives(status):
    assert Charger.availability_state_from_status(status) == "Inoperative"


@pytest.mark.parametrize("status", ["  ", "Offline", None])
def test_availability_state_from_status_unknown(status):
    assert Charger.availability_state_from_status(status) is None
