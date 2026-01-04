import pytest
from django.contrib.auth import get_user_model

from apps.dns.models import DNSProviderCredential, GoDaddyDNSRecord


@pytest.mark.django_db
def test_dns_provider_auth_and_base_url():
    user = get_user_model().objects.create(username="dns-owner")
    credentials = DNSProviderCredential.objects.create(
        user=user,
        api_key="key",
        api_secret="secret",
        use_sandbox=True,
    )

    assert credentials.get_auth_header() == "sso-key key:secret"
    assert credentials.get_base_url() == "https://api.ote-godaddy.com"

    credentials.use_sandbox = False
    assert credentials.get_base_url() == "https://api.godaddy.com"


@pytest.mark.django_db
def test_dns_record_uses_credentials_defaults():
    user = get_user_model().objects.create(username="dns-owner-two")
    credentials = DNSProviderCredential.objects.create(
        user=user,
        api_key="key",
        api_secret="secret",
        default_domain="example.com",
    )

    record = GoDaddyDNSRecord.objects.create(
        credentials=credentials,
        domain="",
        name="@",
        record_type=GoDaddyDNSRecord.Type.A,
        data="127.0.0.1",
    )

    assert record.get_domain(credentials) == "example.com"
    assert record.fqdn(credentials) == "example.com"

    new_credentials = DNSProviderCredential.objects.create(
        user=get_user_model().objects.create(username="other-owner"),
        api_key="key2",
        api_secret="secret2",
        default_domain="example.net",
    )
    record.mark_deployed(credentials=new_credentials)
    record.refresh_from_db()

    assert record.credentials_id == new_credentials.id
    assert record.last_synced_at is not None
    assert record.last_error == ""
