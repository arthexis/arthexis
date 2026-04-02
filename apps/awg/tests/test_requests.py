from __future__ import annotations

import pytest

from apps.awg.models import CableSize
from apps.awg.views.requests import find_awg, find_conduit


@pytest.mark.django_db
def test_find_awg_supports_awg_28_for_low_voltage_and_current():
    CableSize.objects.create(
        awg_size="28",
        material="cu",
        dia_in=0.0126,
        dia_mm=0.321,
        area_kcmil=0.16,
        area_mm2=0.081,
        k_ohm_km=212.898,
        k_ohm_kft=64.8928,
        amps_60c=1,
        amps_75c=1,
        amps_90c=2,
        line_num=1,
    )

    result = find_awg(meters=1, amps=1, volts=120, material="cu", phases=1, ground=1)

    assert result["awg"] == "28"


@pytest.mark.django_db
def test_find_awg_rejects_values_below_new_minimums():
    with pytest.raises(AssertionError):
        find_awg(meters=5, amps=0, volts=120, material="cu")

    with pytest.raises(AssertionError):
        find_awg(meters=5, amps=1, volts=11, material="cu")


@pytest.mark.django_db
def test_find_conduit_returns_na_for_unsupported_small_awg_field():
    assert find_conduit(22, cables=3, conduit="emt") == {"size_inch": "n/a"}
