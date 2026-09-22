from django.test import TestCase

from apps.ocpp.domain.notifications import record_certificate
from tests.apps.ocpp.builders import charger


class CertificateRecordTests(TestCase):
    def test_certificate_metadata_does_not_require_private_material(self) -> None:
        certificate = record_certificate(
            charger=charger("charger-1"),
            fingerprint="sha256:example",
            certificate_type="ChargingStationCertificate",
        )

        self.assertEqual(certificate.fingerprint, "sha256:example")
