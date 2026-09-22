"""OCPP 2.0.1 certificate and ISO 15118 acknowledgement handlers."""

import hashlib
import json
from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async

from apps.ocpp.domain.notifications import record_certificate, record_notification
from apps.ocpp.models import Charger

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class CertificateActions:
    """Record certificate request metadata without retaining certificate material."""

    def __init__(self, charger: Charger) -> None:
        self.charger = charger
        self.handlers: dict[str, Handler] = {
            "Get15118EVCertificate": self.iso15118_certificate,
            "GetCertificateStatus": self.certificate_status,
            "SignCertificate": self.sign_certificate,
        }

    async def iso15118_certificate(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        await self._record("Get15118EVCertificate", payload, "iso15118", "failed")
        return {"status": "Failed", "exiResponse": ""}

    async def certificate_status(self, payload: dict[str, object]) -> dict[str, object]:
        await self._record("GetCertificateStatus", payload, "ocsp", "accepted")
        return {"status": "Accepted"}

    async def sign_certificate(self, payload: dict[str, object]) -> dict[str, object]:
        csr = payload.get("csr")
        if not isinstance(csr, str) or not csr:
            raise ValueError("csr is required")
        await self._record("SignCertificate", payload, "csr", "accepted")
        return {"status": "Accepted"}

    async def _record(
        self,
        action: str,
        payload: dict[str, object],
        certificate_type: str,
        status: str,
    ) -> None:
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        await sync_to_async(record_certificate)(
            charger=self.charger,
            fingerprint=fingerprint,
            certificate_type=certificate_type,
            status=status,
        )
        await sync_to_async(record_notification)(
            charger=self.charger,
            action=action,
            payload={"fingerprint": fingerprint},
        )
