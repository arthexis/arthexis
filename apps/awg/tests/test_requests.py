from __future__ import annotations

import pytest

from apps.awg.models import CableSize
from apps.awg.views.requests import find_awg, find_conduit


@pytest.mark.django_db
def test_find_awg_supports_awg_22_for_low_voltage_and_current():
    CableSize.objects.create(
        awg_size="22",
        material="cu",
        dia_in=0.0253,
        dia_mm=0.644,
        area_kcmil=0.64,
        area_mm2=0.33,
        k_ohm_km=52.962,
        k_ohm_kft=16.1429,
        amps_60c=3,
        amps_75c=5,
        amps_90c=7,
        line_num=1,
    )

    result = find_awg(meters=1, amps=1, volts=12, material="cu", phases=1, ground=1)

    assert result["awg"] == "22"


@pytest.mark.django_db
def test_find_awg_rejects_values_below_new_minimums():
    with pytest.raises(AssertionError):
        find_awg(meters=5, amps=0, volts=12, material="cu")

    with pytest.raises(AssertionError):
        find_awg(meters=5, amps=1, volts=11, material="cu")


@pytest.mark.django_db
def test_find_conduit_returns_na_for_unsupported_small_awg_field():
    assert find_conduit(22, cables=3, conduit="emt") == {"size_inch": "n/a"}
