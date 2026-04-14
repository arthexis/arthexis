from __future__ import annotations

from django.test import override_settings

from apps.ocpp.services import certificate_status


class _FakeResponse:
    def __init__(self, *, status_code: int, payload=None, json_error: Exception | None = None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeSession:
    last_instance = None

    def __init__(self, response=None, request_error: Exception | None = None):
        self.mounted: dict[str, object] = {}
        self.request_calls: list[tuple[str, str, dict[str, object]]] = []
        self._response = response
        self._request_error = request_error
        _FakeSession.last_instance = self

    def mount(self, prefix: str, adapter: object) -> None:
        self.mounted[prefix] = adapter

    def request(self, method: str, url: str, **kwargs):
        self.request_calls.append((method, url, kwargs))
        if self._request_error is not None:
            raise self._request_error
        return self._response

    def close(self):
        return None


@override_settings(OCPP_CERT_STATUS_TIMEOUT_SECONDS=9, OCPP_CERT_STATUS_RETRIES=4)
def test_request_with_retry_uses_configured_retry_session(monkeypatch):
    expected_response = _FakeResponse(status_code=200, payload={"status": "good"})
    monkeypatch.setattr(
        certificate_status.requests,
        "Session",
        lambda: _FakeSession(response=expected_response),
    )

    payload, error = certificate_status._request_with_retry(
        method="post",
        url="https://ocsp.example.test/status",
        payload={"certificateHashData": {"serialNumber": "AA"}},
    )

    assert error == ""
    assert payload == {"status": "good"}

    session = _FakeSession.last_instance
    assert session is not None
    assert session.request_calls[0][0] == "post"
    assert session.request_calls[0][2]["timeout"] == 9
    https_adapter = session.mounted["https://"]
    retry = https_adapter.max_retries
    assert retry.total == 4
    assert retry.connect == 4
    assert retry.read == 4
    assert retry.status == 4


@override_settings(OCPP_CERT_STATUS_OCSP_URL="https://ocsp.example.test/status")
def test_check_ocsp_timeout_path_is_unchanged(monkeypatch):
    monkeypatch.setattr(
        certificate_status.requests,
        "Session",
        lambda: _FakeSession(request_error=certificate_status.requests.Timeout("timed out")),
    )

    ocsp_data, ocsp_error = certificate_status._check_ocsp(
        {
            "hashAlgorithm": "SHA256",
            "issuerKeyHash": "def",
            "issuerNameHash": "abc",
            "serialNumber": "AA",
        }
    )

    assert ocsp_data["status"] == "unknown"
    assert ocsp_data["responderUrl"] == "https://ocsp.example.test/status"
    assert ocsp_data["errors"]
    assert "OCSP responder unavailable: Request timed out." == ocsp_error


@override_settings(OCPP_CERT_STATUS_OCSP_URL="https://ocsp.example.test/status")
def test_check_ocsp_http_error_path_is_unchanged(monkeypatch):
    monkeypatch.setattr(
        certificate_status.requests,
        "Session",
        lambda: _FakeSession(
            response=_FakeResponse(
                status_code=503,
                payload={"error": "upstream unavailable"},
            )
        ),
    )

    _ocsp_data, ocsp_error = certificate_status._check_ocsp(
        {
            "hashAlgorithm": "SHA256",
            "issuerKeyHash": "def",
            "issuerNameHash": "abc",
            "serialNumber": "AA",
        }
    )

    assert ocsp_error == "OCSP responder unavailable: upstream unavailable"


@override_settings(OCPP_CERT_STATUS_CRL_URL="https://crl.example.test/status")
def test_check_crl_invalid_json_path_is_unchanged(monkeypatch):
    monkeypatch.setattr(
        certificate_status.requests,
        "Session",
        lambda: _FakeSession(
            response=_FakeResponse(status_code=200, json_error=ValueError("invalid json"))
        ),
    )

    revoked, crl_error = certificate_status._check_crl(
        {
            "hashAlgorithm": "SHA256",
            "issuerKeyHash": "def",
            "issuerNameHash": "abc",
            "serialNumber": "AA",
        }
    )

    assert revoked is False
    assert crl_error == "CRL responder unavailable: Responder returned invalid JSON."
