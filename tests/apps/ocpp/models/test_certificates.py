import pytest

from apps.ocpp.domain.notifications import record_certificate
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_certificate_metadata_does_not_require_private_material() -> None:
    certificate = record_certificate(
        charger=charger("charger-1"),
        fingerprint="sha256:example",
        certificate_type="ChargingStationCertificate",
    )

    assert certificate.fingerprint == "sha256:example"
