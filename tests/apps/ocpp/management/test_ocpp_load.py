from io import StringIO
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ocpp.management.commands.ocpp_load import Command
from apps.ocpp.models import Charger


@pytest.fixture
def load_charger():
    def create(identity: str) -> Charger:
        return Charger.objects.create(
            identity=identity,
            authority_cutover_at="2026-09-27T12:00:00Z",
        )

    return create


@pytest.fixture
def synthetic_result(monkeypatch):
    def set_result(**overrides):
        result = {
            "source": "synthetic",
            "live_failures": 0,
            "max_live_latency_seconds": 0.1,
        }
        result.update(overrides)
        monkeypatch.setattr(Command, "_run_synthetic", lambda self, charger, options: result)

    return set_result


@pytest.mark.django_db
def test_load_command_marks_live_latency_success(load_charger, synthetic_result) -> None:
    load_charger("load-pass")
    synthetic_result()
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
def test_load_command_fails_when_live_latency_exceeds_threshold(load_charger, synthetic_result) -> None:
    load_charger("load-fail")
    synthetic_result(max_live_latency_seconds=0.75)
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
def test_load_command_fails_when_live_probe_fails(load_charger, synthetic_result) -> None:
    load_charger("load-error")
    synthetic_result(live_failures=1)

    with pytest.raises(CommandError, match="Live OCPP health threshold failed"):
        call_command(
            "ocpp_load",
            charger="load-error",
            synthetic=True,
            max_live_latency=0.5,
        )


@pytest.mark.django_db
def test_load_command_rejects_invalid_reconnect_checkpoint(load_charger, synthetic_result) -> None:
    load_charger("load-reconnect-invalid")
    synthetic_result()

    with pytest.raises(CommandError, match="--reconnect-after must be positive"):
        call_command(
            "ocpp_load",
            charger="load-reconnect-invalid",
            synthetic=True,
            reconnect_after=0,
        )
