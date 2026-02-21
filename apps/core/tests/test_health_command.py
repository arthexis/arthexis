import io

import pytest

from django.core.management import call_command

from apps.core.management.commands import health as health_command
from apps.core.services.health import HealthCheckDefinition

pytestmark = pytest.mark.critical


@pytest.mark.django_db
def test_health_runs_core_time_target():
    stream = io.StringIO()

    call_command("health", target=["core.time"], stdout=stream)

    output = stream.getvalue()
    assert "[core.time]" in output
    assert "Current server time:" in output
    assert "Health checks passed." in output


@pytest.mark.django_db
def test_health_group_core_runs_non_interactive_checks(monkeypatch):
    stream = io.StringIO()

    def _ok_runner(**kwargs):
        kwargs["stdout"].write("ok")

    patched = {}
    for target, definition in list(health_command.HEALTH_CHECKS.items()):
        if definition.group != "core" or not definition.include_in_group:
            continue
        patched[target] = HealthCheckDefinition(
            target=definition.target,
            group=definition.group,
            description=definition.description,
            runner=_ok_runner,
            include_in_group=definition.include_in_group,
        )

    for target, definition in patched.items():
        monkeypatch.setitem(health_command.HEALTH_CHECKS, target, definition)

    call_command("health", group=["core"], stdout=stream)

    output = stream.getvalue()
    assert "core.lcd_send" not in output
    assert "core.rfid" not in output
    assert "Health checks passed." in output


def test_check_time_wrapper_delegates_to_health(monkeypatch):
    stream = io.StringIO()
    err_stream = io.StringIO()
    captured = {}

    def _fake_call_command(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs

    monkeypatch.setattr("apps.core.management.commands.check_time.call_command", _fake_call_command)

    call_command("check_time", stdout=stream, stderr=err_stream)

    assert captured["name"] == "health"
    assert captured["kwargs"]["target"] == ["core.time"]
