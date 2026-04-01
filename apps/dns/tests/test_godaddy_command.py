"""Tests for the GoDaddy credential management command."""

from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.dns.models import DNSProviderCredential


@pytest.mark.django_db
def test_godaddy_add_and_list():
    """Add should create a credential and list should show it."""

    user = get_user_model().objects.create_user(username="dns-admin")

    add_stdout = StringIO()
    call_command(
        "godaddy",
        "add",
        user=user.username,
        api_key="api-key",
        api_secret="api-secret",
        customer_id="cust-1",
        default_domain="example.com",
        sandbox=True,
        stdout=add_stdout,
    )

    assert "Added GoDaddy credential" in add_stdout.getvalue()

    credential = DNSProviderCredential.objects.get()
    assert credential.user == user
    assert credential.get_default_domain() == "example.com"
    assert credential.use_sandbox is True

    list_stdout = StringIO()
    call_command("godaddy", "list", stdout=list_stdout)
    output = list_stdout.getvalue()

    assert f"{credential.pk}:" in output
    assert "owner=dns-admin" in output
    assert "env=sandbox" in output


@pytest.mark.django_db
@pytest.mark.integration
def test_godaddy_remove_deletes_existing_credential():
    """Remove should delete an existing GoDaddy credential."""

    user = get_user_model().objects.create_user(username="dns-owner")
    credential = DNSProviderCredential.objects.create(
        user=user,
        api_key="k",
        api_secret="s",
    )

    stdout = StringIO()
    call_command("godaddy", "remove", str(credential.pk), stdout=stdout)

    assert "Removed GoDaddy credential" in stdout.getvalue()
    assert not DNSProviderCredential.objects.filter(pk=credential.pk).exists()


@pytest.mark.django_db
def test_godaddy_add_requires_known_user():
    """Regression: add should fail with a specific error when the user does not exist."""

    with pytest.raises(CommandError, match="User 'missing-user' does not exist"):
        call_command(
            "godaddy",
            "add",
            user="missing-user",
            api_key="api-key",
            api_secret="api-secret",
        )


@pytest.mark.django_db
def test_godaddy_remove_requires_credential_id():
    """Remove should fail when no credential id is provided."""

    with pytest.raises(CommandError, match="remove requires credential_id"):
        call_command("godaddy", "remove")


@pytest.mark.django_db
def test_godaddy_remove_errors_when_credential_not_found():
    """Remove should fail when the credential id does not exist."""

    with pytest.raises(CommandError, match="GoDaddy credential #9999"):
        call_command("godaddy", "remove", "9999")


@pytest.mark.django_db
def test_godaddy_setup_reports_manual_dns_instructions():
    """Setup should direct operators to manual DNS configuration."""

    with pytest.raises(CommandError, match="Automated GoDaddy DNS setup was removed"):
        call_command(
            "godaddy",
            "setup",
            api_key="stored-key",
            api_secret="stored-secret",
        )


@pytest.mark.django_db
def test_godaddy_verify_checks_selected_credential(monkeypatch):
    """Verify should call GoDaddy API with selected enabled credential."""

    DNSProviderCredential.objects.create(
        api_key="verify-key",
        api_secret="verify-secret",
        default_domain="example.com",
    )

    captured: dict[str, object] = {}

    def _fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr("apps.dns.management.commands.godaddy.requests.get", _fake_get)
    stdout = StringIO()
    call_command("godaddy", "verify", key="verify-key", stdout=stdout)

    assert captured["url"] == "https://api.godaddy.com/v1/domains/example.com"
    assert str(captured["headers"]["Authorization"]).startswith("sso-key ")
    assert "verified for example.com" in stdout.getvalue()


@pytest.mark.django_db
def test_godaddy_verify_uses_json_message_for_error(monkeypatch):
    """Verify should surface concise API error details from JSON responses."""

    DNSProviderCredential.objects.create(
        api_key="verify-error-key",
        api_secret="verify-error-secret",
        default_domain="example.com",
    )

    monkeypatch.setattr(
        "apps.dns.management.commands.godaddy.requests.get",
        lambda url, headers, timeout: SimpleNamespace(
            status_code=403,
            text='{"code":"FORBIDDEN","message":"Invalid API key"}',
            json=lambda: {"code": "FORBIDDEN", "message": "Invalid API key"},
        ),
    )

    with pytest.raises(CommandError, match="403 Invalid API key"):
        call_command("godaddy", "verify", key="verify-error-key")


@pytest.mark.django_db
def test_godaddy_verify_flag_alias_runs_verification(monkeypatch):
    """--verify should behave as an alias for verify action."""

    DNSProviderCredential.objects.create(
        api_key="verify-flag-key",
        api_secret="verify-flag-secret",
        default_domain="example.com",
    )

    monkeypatch.setattr(
        "apps.dns.management.commands.godaddy.requests.get",
        lambda url, headers, timeout: SimpleNamespace(status_code=200, text="ok"),
    )
    stdout = StringIO()
    call_command("godaddy", verify=True, stdout=stdout)

    assert "verified for example.com" in stdout.getvalue()
