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


def test_base_vdrop_uses_two_wire_multiplier_for_two_phase():
    assert _base_vdrop(_params(phases=2)) == _base_vdrop(_params(phases=1))


def test_base_vdrop_uses_three_phase_multiplier_for_three_phase():
    assert _base_vdrop(_params(phases=3)) < _base_vdrop(_params(phases=1))
