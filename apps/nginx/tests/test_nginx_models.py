from datetime import timedelta

import pytest
from django.utils import timezone

from apps.nginx import services
from apps.nginx.models import SiteConfiguration


@pytest.mark.django_db
def test_site_configuration_apply_records_state(monkeypatch):
    config = SiteConfiguration.get_default()
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)

    captured = {}

    def fake_apply_nginx_configuration(**kwargs):
        captured.update(kwargs)
        return services.ApplyResult(
            changed=True,
            validated=True,
            reloaded=True,
            message="applied",
        )

    monkeypatch.setattr(services, "apply_nginx_configuration", fake_apply_nginx_configuration)

    result = config.apply()

    assert result.message == "applied"
    config.refresh_from_db()
    assert config.last_applied_at == now
    assert config.last_validated_at == now
    assert captured["mode"] == config.mode
    assert captured["port"] == config.port
    assert captured["role"] == config.role
    assert captured["destination"] == config.expected_destination


@pytest.mark.django_db
def test_site_configuration_validate_only(monkeypatch):
    config = SiteConfiguration.get_default()
    later = timezone.now() + timedelta(minutes=5)
    monkeypatch.setattr(timezone, "now", lambda: later)

    def fake_restart_nginx(**kwargs):
        return services.ApplyResult(
            changed=False,
            validated=False,
            reloaded=False,
            message="restarted",
        )

    monkeypatch.setattr(services, "restart_nginx", fake_restart_nginx)

    result = config.validate_only()
    assert result.message == "restarted"
    config.refresh_from_db()
    assert config.last_validated_at == later
    assert config.last_message == "restarted"
