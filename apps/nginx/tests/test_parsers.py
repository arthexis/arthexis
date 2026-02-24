import pytest
from django.core.exceptions import ValidationError

from apps.nginx.parsers import (
    _detect_external_websockets,
    _detect_https_enabled,
    _detect_ipv6_enabled,
    _extract_proxy_port,
    _extract_server_name,
    parse_subdomain_prefixes,
)


@pytest.mark.critical
def test_parse_subdomain_prefixes_normalizes_and_deduplicates():
    parsed = parse_subdomain_prefixes("API, admin admin,api")
    assert parsed == ["api", "admin"]


@pytest.mark.critical
def test_parse_subdomain_prefixes_strict_raises_for_invalid_tokens():
    with pytest.raises(ValidationError, match="Invalid subdomain prefixes"):
        parse_subdomain_prefixes("ok, bad.token, -bad")


@pytest.mark.critical
def test_extract_proxy_port_ignores_invalid_values():
    content = "proxy_pass http://127.0.0.1:70000;\nproxy_pass http://127.0.0.1:8443;"
    assert _extract_proxy_port(content) == 8443


@pytest.mark.critical
def test_extract_server_name_skips_wildcards_and_underscore():
    content = "server_name _ *.example.com .example.com real.example.com;"
    assert _extract_server_name(content) == "real.example.com"


@pytest.mark.critical
def test_https_ipv6_and_websocket_detectors():
    content = """
        listen 443 ssl;
        listen [::]:443 ssl;
        proxy_set_header Connection $connection_upgrade;
    """
    assert _detect_https_enabled(content) is True
    assert _detect_ipv6_enabled(content) is True
    assert _detect_external_websockets(content) is True
