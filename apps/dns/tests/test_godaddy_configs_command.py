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
    assert "ab****34" in output


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
