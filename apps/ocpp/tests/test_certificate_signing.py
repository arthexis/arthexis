import pytest

from apps.ocpp.services import certificate_signing


def test_sign_certificate_request_handles_non_mapping_error(settings, monkeypatch):
    settings.CERTIFICATE_SIGNING_URL = "http://signer"

    class DummyResponse:
        status_code = 400
        reason = "Bad Request"

        @staticmethod
        def json():
            return "invalid"

    def fake_post(url, json=None, timeout=None):  # noqa: A002
        assert url == "http://signer"
        return DummyResponse()

    monkeypatch.setattr(certificate_signing.requests, "post", fake_post)

    with pytest.raises(certificate_signing.CertificateSigningError) as excinfo:
        certificate_signing.sign_certificate_request(csr="CSR")

    assert "invalid" in str(excinfo.value)
