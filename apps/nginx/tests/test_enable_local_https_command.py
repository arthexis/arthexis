import pytest
from django.core.management import call_command

from apps.certs.models import SelfSignedCertificate
from apps.nginx import services
from apps.nginx.models import SiteConfiguration


@pytest.mark.django_db
def test_enable_local_https_command_creates_local_config(monkeypatch):
    captured = {}

    def fake_generate(self, *, sudo="sudo", subject_alt_names=None):
        captured["sudo"] = sudo
        captured["subject_alt_names"] = subject_alt_names
        return "generated"

    def fake_apply(self, reload=True, remove=False):
        captured["reload"] = reload
        return services.ApplyResult(
            changed=True,
            validated=True,
            reloaded=True,
            message="applied",
        )

    monkeypatch.setattr(SelfSignedCertificate, "generate", fake_generate)
    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("enable_local_https", "--no-reload", "--no-sudo")

    config = SiteConfiguration.objects.get(name="localhost")
    assert config.protocol == "https"
    assert config.enabled is True
    assert config.certificate is not None
    assert isinstance(config.certificate._specific_certificate, SelfSignedCertificate)
    assert config.certificate.domain == "localhost"
    assert captured["subject_alt_names"] == ["localhost", "127.0.0.1", "::1"]
    assert captured["sudo"] == ""
    assert captured["reload"] is False
