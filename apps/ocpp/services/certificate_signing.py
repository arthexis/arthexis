from __future__ import annotations

import requests
from django.conf import settings


class CertificateSigningError(Exception):
    """Raised when the signing service cannot fulfil a request."""


def sign_certificate_request(
    *, csr: str, certificate_type: str = "", charger_id: str | None = None
) -> str:
    """Send the CSR to the configured signing service and return the chain.

    The signing service URL must be provided via ``CERTIFICATE_SIGNING_URL`` in
    Django settings. Responses must include a ``certificateChain`` value.
    """

    service_url = getattr(settings, "CERTIFICATE_SIGNING_URL", "").strip()
    if not service_url:
        raise CertificateSigningError("Signing service unavailable.")

    payload: dict[str, object] = {"csr": csr}
    if certificate_type:
        payload["certificateType"] = certificate_type
    if charger_id:
        payload["chargerId"] = charger_id

    try:
        response = requests.post(service_url, json=payload, timeout=10)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise CertificateSigningError(str(exc) or "Signing request failed.") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise CertificateSigningError("Invalid signing service response.") from exc

    if not isinstance(data, dict):
        message = str(data).strip()
        if response.status_code >= 400:
            raise CertificateSigningError(message or "Signing service rejected request.")
        raise CertificateSigningError("Signing service returned invalid response payload.")

    if response.status_code >= 400:
        message = data.get("detail") or data.get("error") or response.reason
        raise CertificateSigningError(message or "Signing service rejected request.")

    certificate_chain = data.get("certificateChain") or data.get("certificate_chain")
    if not isinstance(certificate_chain, str) or not certificate_chain.strip():
        raise CertificateSigningError("Signing service returned no certificate chain.")

    return certificate_chain.strip()

