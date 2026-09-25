from datetime import datetime, timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


@pytest.fixture
def legacy_charger():
    return charger("legacy-charger")


def test_show_reports_unset_cutover_without_mutation(legacy_charger) -> None:
    output = StringIO()

    call_command("ocpp_cutover", legacy_charger.identity, stdout=output)

    legacy_charger.refresh_from_db()
    assert legacy_charger.authority_cutover_at is None
    assert output.getvalue().strip() == "legacy-charger: authority cutover unset"


def test_set_persists_explicit_offset_aware_cutover(legacy_charger) -> None:
    output = StringIO()

    call_command(
        "ocpp_cutover",
        legacy_charger.identity,
        "--set",
        "2026-09-21T14:30:00-06:00",
        stdout=output,
    )

    legacy_charger.refresh_from_db()
    assert legacy_charger.authority_cutover_at == datetime(
        2026, 9, 21, 20, 30, tzinfo=timezone.utc
    )
    assert "authority cutover" in output.getvalue()


def test_set_accepts_z_suffix(legacy_charger) -> None:
    call_command(
        "ocpp_cutover",
        legacy_charger.identity,
        "--set",
        "2026-09-22T12:00:00Z",
        stdout=StringIO(),
    )

    legacy_charger.refresh_from_db()
    assert legacy_charger.authority_cutover_at == datetime(
        2026, 9, 22, 12, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            "2026-09-22T12:00:00",
            "Cutover must include a timezone offset or Z suffix",
        ),
        ("yesterday", "Cutover must be a valid ISO-8601 timestamp"),
    ],
)
def test_set_rejects_invalid_cutover_values(
    legacy_charger,
    value: str,
    message: str,
) -> None:
    with pytest.raises(CommandError, match=message):
        call_command(
            "ocpp_cutover",
            legacy_charger.identity,
            "--set",
            value,
        )

    legacy_charger.refresh_from_db()
    assert legacy_charger.authority_cutover_at is None


def test_clear_removes_existing_cutover(legacy_charger) -> None:
    legacy_charger.authority_cutover_at = datetime(
        2026, 9, 22, 12, tzinfo=timezone.utc
    )
    legacy_charger.save(update_fields=("authority_cutover_at",))
    output = StringIO()

    call_command(
        "ocpp_cutover",
        legacy_charger.identity,
        "--clear",
        stdout=output,
    )

    legacy_charger.refresh_from_db()
    assert legacy_charger.authority_cutover_at is None
    assert output.getvalue().strip() == "legacy-charger: authority cutover unset"


def test_unknown_charger_is_rejected() -> None:
    with pytest.raises(CommandError, match="Unknown charger: missing"):
        call_command("ocpp_cutover", "missing")


def test_set_and_clear_are_mutually_exclusive(legacy_charger) -> None:
    with pytest.raises(CommandError, match="not allowed with argument"):
        call_command(
            "ocpp_cutover",
            legacy_charger.identity,
            "--set",
            "2026-09-22T12:00:00Z",
            "--clear",
        )
