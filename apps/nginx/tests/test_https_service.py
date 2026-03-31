from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.nginx.management.commands.https_parts.service import HttpsProvisioningService


def _service() -> HttpsProvisioningService:
    command = SimpleNamespace(
        stdout=SimpleNamespace(write=lambda _message: None),
        style=SimpleNamespace(SUCCESS=lambda message: message),
    )
    return HttpsProvisioningService(command=command)


def test_parse_public_ip_accepts_global_ipv4():
    service = _service()
    assert service._parse_public_ip("8.8.8.8") == "8.8.8.8"


@pytest.mark.parametrize("address", ["10.0.0.1", "127.0.0.1", "169.254.1.2", "fe80::1"])
def test_parse_public_ip_rejects_non_public_values(address):
    service = _service()
    with pytest.raises(CommandError, match="public-routable"):
        service._parse_public_ip(address)


def test_zone_and_name_uses_default_domain_for_subdomain():
    service = _service()
    credential = SimpleNamespace(get_default_domain=lambda: "arthexis.com")

    assert service._zone_and_name(domain="api.arthexis.com", credential=credential) == (
        "arthexis.com",
        "api",
    )


def test_zone_and_name_rejects_domain_outside_default_domain():
    service = _service()
    credential = SimpleNamespace(get_default_domain=lambda: "arthexis.com")

    with pytest.raises(CommandError, match="does not match credential default domain"):
        service._zone_and_name(domain="example.com", credential=credential)


def test_zone_and_name_matches_default_domain_case_insensitively():
    service = _service()
    credential = SimpleNamespace(get_default_domain=lambda: "ARTHEXIS.COM")

    assert service._zone_and_name(domain="Api.Arthexis.Com", credential=credential) == (
        "arthexis.com",
        "api",
    )


def test_zone_and_name_requires_default_domain_when_missing():
    service = _service()
    credential = SimpleNamespace(get_default_domain=lambda: "")

    with pytest.raises(CommandError, match="default domain is required"):
        service._zone_and_name(domain="api.example.co.uk", credential=credential)


def test_upsert_godaddy_site_record_publishes_a_record(monkeypatch):
    service = _service()
    credential = SimpleNamespace(
        provider="godaddy",
        get_default_domain=lambda: "arthexis.com",
        get_base_url=lambda: "https://api.godaddy.com",
        get_auth_header=lambda: "sso-key abc:def",
        get_customer_id=lambda: "",
    )
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200
        text = ""

    monkeypatch.setattr(
        "apps.nginx.management.commands.https_parts.service._resolve_godaddy_credential",
        lambda key=None: credential,
    )

    def fake_put(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("apps.nginx.management.commands.https_parts.service.requests.put", fake_put)

    service._upsert_godaddy_site_record(
        domain="arthexis.com",
        static_ip="23.23.51.205",
        key=None,
        sandbox_override=False,
    )

    assert captured["url"] == "https://api.godaddy.com/v1/domains/arthexis.com/records/A/@"
    assert captured["json"] == [{"data": "23.23.51.205", "ttl": 600}]


def test_upsert_godaddy_site_record_honors_sandbox_override(monkeypatch):
    service = _service()
    credential = SimpleNamespace(
        provider="godaddy",
        get_default_domain=lambda: "arthexis.com",
        get_base_url=lambda: "https://api.godaddy.com",
        get_auth_header=lambda: "sso-key abc:def",
        get_customer_id=lambda: "",
    )
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200
        text = ""

    def fake_put(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("apps.nginx.management.commands.https_parts.service.requests.put", fake_put)
    monkeypatch.setattr(
        "apps.nginx.management.commands.https_parts.service._resolve_godaddy_credential",
        lambda key=None: pytest.fail("unexpected credential re-resolution"),
    )

    service._upsert_godaddy_site_record(
        domain="arthexis.com",
        static_ip="23.23.51.205",
        key=None,
        credential=credential,
        sandbox_override=True,
    )

    assert captured["url"] == "https://api.ote-godaddy.com/v1/domains/arthexis.com/records/A/@"
