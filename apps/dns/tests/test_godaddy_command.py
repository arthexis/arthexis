"""Tests for the GoDaddy credential management command."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

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
