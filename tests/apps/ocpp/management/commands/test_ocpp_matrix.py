from io import StringIO

from django.core.management import call_command

from apps.ocpp.protocol.registry import ALL_ACTIONS


def test_app_wide_report_states_the_complete_matrix() -> None:
    output = StringIO()

    call_command("ocpp_matrix", stdout=output)

    assert f"{len(ALL_ACTIONS)} / {len(ALL_ACTIONS)} implemented" in output.getvalue()
