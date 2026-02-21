from unittest.mock import patch

import pytest
from django.core.management import call_command


pytestmark = pytest.mark.integration


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("legacy_name", "expected"),
    [
        ("rfid_service", "service"),
        ("watch_rfid", "watch"),
        ("rfid_doctor", "doctor"),
        ("import_rfids", "import"),
        ("export_rfids", "export"),
    ],
)
def test_legacy_cards_commands_delegate_to_canonical_rfid(legacy_name, expected):
    with patch("django.core.management.call_command") as call_mock:
        if legacy_name in {"import_rfids", "export_rfids"}:
            call_command(legacy_name, "/tmp/rfids.csv")
        else:
            call_command(legacy_name)

    assert call_mock.call_args[0][0] == "rfid"
    assert call_mock.call_args[0][1] == expected


@pytest.mark.django_db
def test_ocpp_rfid_check_uses_shared_implementation(capsys):
    with patch(
        "apps.cards.management.commands._rfid_check_impl.validate_rfid_value",
        return_value={"rfid": "ABCD"},
    ):
        call_command("rfid_check", uid="ABCD")

    output = capsys.readouterr()
    assert "deprecated" in output.err.lower()
    assert '"rfid": "ABCD"' in output.out
