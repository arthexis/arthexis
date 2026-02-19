from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.dns.models import DNSProviderCredential


@pytest.mark.django_db
def test_godaddy_configs_lists_credentials():
    """The command should list all stored GoDaddy credentials."""

    user = get_user_model().objects.create(username="dns-list")
    DNSProviderCredential.objects.create(
        user=user,
        api_key="abcd1234",
        api_secret="secret1234",
        default_domain="example.com",
        customer_id="cust-1",
    )

    stdout = StringIO()
    call_command("godaddy_configs", stdout=stdout)

    output = stdout.getvalue()
    assert "GoDaddy credentials:" in output
    assert "default_domain=example.com" in output
    assert "customer_id=cust-1" in output
    assert "a******4" in output


@pytest.mark.django_db
def test_godaddy_configs_lists_empty_credentials_message():
    """The command should print a clear message when no credentials exist."""

    stdout = StringIO()
    call_command("godaddy_configs", stdout=stdout)

    assert "No GoDaddy credentials configured." in stdout.getvalue()


@pytest.mark.django_db
def test_godaddy_configs_requires_credential_id_for_edits():
    """Edit flags should require selecting a credential id."""

    with pytest.raises(CommandError, match="--credential-id is required"):
        call_command("godaddy_configs", api_key="new")


@pytest.mark.django_db
def test_godaddy_configs_updates_selected_credential():
    """The command should update selected fields for a credential."""

    user = get_user_model().objects.create(username="dns-update")
    credential = DNSProviderCredential.objects.create(
        user=user,
        api_key="oldkey",
        api_secret="oldsecret",
        default_domain="old.example.com",
        customer_id="old-customer",
        is_enabled=True,
        use_sandbox=False,
    )

    stdout = StringIO()
    call_command(
        "godaddy_configs",
        credential_id=credential.id,
        api_key="newkey",
        api_secret="newsecret",
        customer_id="new-customer",
        default_domain="new.example.com",
        disable=True,
        sandbox=True,
        stdout=stdout,
    )

    credential.refresh_from_db()

    assert credential.api_key == "newkey"
    assert credential.api_secret == "newsecret"
    assert credential.customer_id == "new-customer"
    assert credential.default_domain == "new.example.com"
    assert credential.is_enabled is False
    assert credential.use_sandbox is True
    assert f"Updated GoDaddy credential id={credential.id}." in stdout.getvalue()


@pytest.mark.django_db
def test_godaddy_configs_raises_for_missing_credential_id_on_update():
    """Updating a missing credential id should raise a CommandError."""

    with pytest.raises(CommandError, match="does not exist"):
        call_command("godaddy_configs", credential_id=9999, default_domain="example.com")


@pytest.mark.django_db
def test_godaddy_configs_warns_when_no_update_flags():
    """Selecting a credential without edit flags should not modify anything."""

    user = get_user_model().objects.create(username="dns-no-update")
    credential = DNSProviderCredential.objects.create(
        user=user,
        api_key="samekey",
        api_secret="samesecret",
        default_domain="same.example.com",
        customer_id="same-customer",
    )

    stdout = StringIO()
    call_command("godaddy_configs", credential_id=credential.id, stdout=stdout)

    output = stdout.getvalue()
    assert "No edit flags were provided; listing credentials without changes." in output


@pytest.mark.django_db
def test_godaddy_configs_supports_environment_variable_secret_updates(monkeypatch):
    """API key and secret can be sourced from environment variables."""

    user = get_user_model().objects.create(username="dns-env-update")
    credential = DNSProviderCredential.objects.create(
        user=user,
        api_key="oldkey",
        api_secret="oldsecret",
    )
    monkeypatch.setenv("GODADDY_API_KEY", "envkey")
    monkeypatch.setenv("GODADDY_API_SECRET", "envsecret")

    call_command(
        "godaddy_configs",
        credential_id=credential.id,
        api_key_env="GODADDY_API_KEY",
        api_secret_env="GODADDY_API_SECRET",
    )

    credential.refresh_from_db()
    assert credential.api_key == "envkey"
    assert credential.api_secret == "envsecret"


@pytest.mark.django_db
def test_godaddy_configs_errors_for_missing_secret_environment_variable():
    """Missing env vars for secrets should raise CommandError."""

    user = get_user_model().objects.create(username="dns-env-missing")
    credential = DNSProviderCredential.objects.create(
        user=user,
        api_key="oldkey",
        api_secret="oldsecret",
    )

    with pytest.raises(CommandError, match="is not set"):
        call_command(
            "godaddy_configs",
            credential_id=credential.id,
            api_key_env="GODADDY_API_KEY_MISSING",
        )
