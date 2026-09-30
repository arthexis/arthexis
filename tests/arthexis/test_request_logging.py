import logging

from django.http import HttpResponse
from django.test import RequestFactory

from arthexis.request_logging import RequestOriginLoggingMiddleware, client_ip


def test_client_ip_uses_forwarded_origin_from_loopback_proxy():
    request = RequestFactory().get(
        "/status?detail=1",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="203.0.113.41, 127.0.0.1",
        HTTP_X_REAL_IP="203.0.113.41",
    )

    assert client_ip(request) == "203.0.113.41"


def test_client_ip_ignores_spoofed_forwarded_headers_from_non_loopback_peer():
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="198.51.100.22",
        HTTP_X_FORWARDED_FOR="203.0.113.41",
        HTTP_X_REAL_IP="203.0.113.41",
    )

    assert client_ip(request) == "198.51.100.22"


def test_client_ip_uses_x_real_ip_when_forwarded_for_is_missing():
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="::1",
        HTTP_X_REAL_IP="192.0.2.9",
    )

    assert client_ip(request) == "192.0.2.9"


def test_middleware_logs_origin_before_downstream_host_validation(caplog):
    request = RequestFactory().get(
        "/probe.php?x=1",
        REMOTE_ADDR="127.0.0.1",
        HTTP_HOST="evergo.gelectriic.com",
        HTTP_X_FORWARDED_FOR="10.42.0.17",
    )
    middleware = RequestOriginLoggingMiddleware(lambda request: HttpResponse("ok"))

    with caplog.at_level(logging.INFO, logger="arthexis.request"):
        response = middleware(request)

    assert response.status_code == 200
    assert "client_ip=10.42.0.17" in caplog.text
    assert "peer_ip=127.0.0.1" in caplog.text
    assert "host=evergo.gelectriic.com" in caplog.text
    assert "path=/probe.php?x=1" in caplog.text
