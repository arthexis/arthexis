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

