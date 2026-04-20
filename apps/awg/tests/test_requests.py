import pytest
from apps.awg.views.requests import _AwgParameters, _base_vdrop


def _params(*, phases: int) -> _AwgParameters:
    return _AwgParameters(
        amps=32,
        meters=25,
        volts=240,
        material="cu",
        max_lines=1,
        phases=phases,
        temperature=None,
        max_awg=None,
        conduit=None,
        ground_label="",
        ground_options=(1,),
    )


@pytest.mark.parametrize(
    ("phases", "comparator"),
    [(2, lambda value, baseline: value == baseline), (3, lambda value, baseline: value < baseline)],
)
def test_base_vdrop_uses_expected_phase_multiplier(phases, comparator):
    base = _base_vdrop(_params(phases=1))
    value = _base_vdrop(_params(phases=phases))
    assert comparator(value, base)
