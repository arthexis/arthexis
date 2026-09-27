from io import StringIO
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ocpp.management.commands.ocpp_load import Command
from apps.ocpp.models import Charger


@pytest.mark.django_db
def test_load_command_marks_live_latency_success(monkeypatch) -> None:
    Charger.objects.create(identity="load-pass", authority_cutover_at="2026-09-27T12:00:00Z")
    monkeypatch.setattr(
        Command,
        "_run_synthetic",
        lambda self, charger, options: {
            "source": "synthetic",
            "live_failures": 0,
            "max_live_latency_seconds": 0.1,
        },
    )
    output = StringIO()

    call_command(
        "ocpp_load",
        charger="load-pass",
        synthetic=True,
        max_live_latency=0.5,
        json_output=True,
        stdout=output,
    )

    assert '"live_latency_ok": true' in output.getvalue()
    assert '"max_live_latency_threshold_seconds": 0.5' in output.getvalue()


@pytest.mark.django_db
def test_load_command_fails_when_live_latency_exceeds_threshold(monkeypatch) -> None:
    Charger.objects.create(identity="load-fail", authority_cutover_at="2026-09-27T12:00:00Z")
    monkeypatch.setattr(
        Command,
        "_run_synthetic",
        lambda self, charger, options: {
            "source": "synthetic",
            "live_failures": 0,
            "max_live_latency_seconds": 0.75,
        },
    )
    output = StringIO()

    with pytest.raises(CommandError, match="Live OCPP health threshold failed"):
        call_command(
            "ocpp_load",
            charger="load-fail",
            synthetic=True,
            max_live_latency=0.5,
            json_output=True,
            stdout=output,
        )

    assert '"live_latency_ok": false' in output.getvalue()


@pytest.mark.django_db
def test_load_command_fails_when_live_probe_fails(monkeypatch) -> None:
    Charger.objects.create(identity="load-error", authority_cutover_at="2026-09-27T12:00:00Z")
    monkeypatch.setattr(
        Command,
        "_run_synthetic",
        lambda self, charger, options: {
            "source": "synthetic",
            "live_failures": 1,
            "max_live_latency_seconds": 0.1,
        },
    )

    with pytest.raises(CommandError, match="Live OCPP health threshold failed"):
        call_command(
            "ocpp_load",
            charger="load-error",
            synthetic=True,
            max_live_latency=0.5,
        )
