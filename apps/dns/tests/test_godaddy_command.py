"""Tests for the GoDaddy credential management command."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.dns.models import DNSProviderCredential


@pytest.mark.django_db
def test_godaddy_defaults_to_list_output_when_empty():
    """The command should list by default and report an empty configuration."""

    stdout = StringIO()
    call_command("godaddy", stdout=stdout)

    assert "No GoDaddy credentials configured." in stdout.getvalue()


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
