from django.apps import apps

from apps.ocpp.pki.models import CertificateBase, SelfSignedCertificate


def test_ocpp_pki_preserves_historical_django_identity():
    config = apps.get_app_config("certs")

    assert config.name == "apps.ocpp.pki"
    assert CertificateBase._meta.app_label == "certs"
    assert CertificateBase._meta.db_table == "certs_certificatebase"
    assert SelfSignedCertificate._meta.db_table == "certs_selfsignedcertificate"
