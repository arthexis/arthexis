import pytest
from django.urls import reverse

from apps.certs.models import CertbotCertificate, SelfSignedCertificate
from apps.nginx import services
from apps.nginx.models import SiteConfiguration
from apps.nginx.renderers import generate_primary_config


@pytest.mark.django_db
def test_preview_view_shows_file_status(admin_client, tmp_path):
    staging = tmp_path / "sites.json"
    site_destination = tmp_path / "sites.conf"
    primary_path = tmp_path / "arthexis.conf"

    config = SiteConfiguration.objects.create(
        name="preview",
        expected_path=str(primary_path),
        site_entries_path=str(staging),
        site_destination=str(site_destination),
        port=8080,
    )

    staging.write_text('[{"domain": "test.example.com", "require_https": false}]', encoding="utf-8")

    primary_content = generate_primary_config(config.mode, config.port, include_ipv6=config.include_ipv6)
    primary_path.write_text(primary_content, encoding="utf-8")

    url = reverse("admin:nginx_siteconfiguration_preview") + f"?ids={config.pk}"
    response = admin_client.get(url)

    assert response.status_code == 200
    rendered = response.content.decode()
    assert str(config.expected_destination) in rendered
    assert str(config.site_destination_path) in rendered
    assert "Existing file already matches this content." in rendered
    assert "File does not exist on disk." in rendered


@pytest.mark.django_db
def test_preview_view_denies_user_without_permission(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="staff-no-view", email="staff@example.com", password="secret"
    )
    user.is_staff = True
    user.save()

    client.force_login(user)

    url = reverse("admin:nginx_siteconfiguration_preview")
    response = client.get(url)

    assert response.status_code == 403


@pytest.mark.django_db
def test_preview_view_applies_configurations(monkeypatch, admin_client):
    config = SiteConfiguration.objects.create(name="apply-preview")

    calls: dict[str, dict[str, int | bool]] = {}

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        calls["kwargs"] = {"reload": reload, "remove": remove, "pk": self.pk}
        return services.ApplyResult(changed=True, validated=True, reloaded=True, message="ok")

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    url = reverse("admin:nginx_siteconfiguration_preview") + f"?ids={config.pk}"
    response = admin_client.post(url, {"ids": str(config.pk)})

    assert response.status_code == 302
    assert response["Location"].endswith(f"?ids={config.pk}")
    assert calls["kwargs"] == {"reload": True, "remove": False, "pk": config.pk}


@pytest.mark.django_db
def test_preview_view_blocks_https_without_certificate(monkeypatch, admin_client):
    config = SiteConfiguration.objects.create(name="https-preview", protocol="https")

    called = {}

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        called["applied"] = True
        return services.ApplyResult(changed=True, validated=True, reloaded=True, message="ok")

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    url = reverse("admin:nginx_siteconfiguration_preview") + f"?ids={config.pk}"
    response = admin_client.post(url, {"ids": str(config.pk)}, follow=True)

    assert called == {}
    rendered = response.content.decode()
    assert "Generate Certificates" in rendered
    assert "requires a linked certificate" in rendered


@pytest.mark.django_db
def test_preview_default_view_creates_default(admin_client, settings):
    settings.ALLOWED_HOSTS = ["admin.example.com", "testserver"]

    url = reverse("admin:nginx_siteconfiguration_preview_default")
    response = admin_client.get(url)

    assert response.status_code == 200
    config = SiteConfiguration.objects.get(name="admin.example.com")
    rendered = response.content.decode()
    assert str(config) in rendered


@pytest.mark.django_db
def test_preview_default_view_applies_configuration(monkeypatch, admin_client):
    config = SiteConfiguration.get_default()

    calls: dict[str, dict[str, int | bool]] = {}

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        calls["kwargs"] = {"reload": reload, "remove": remove, "pk": self.pk}
        return services.ApplyResult(changed=True, validated=True, reloaded=True, message="ok")

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    url = reverse("admin:nginx_siteconfiguration_preview_default")
    response = admin_client.post(url, {"ids": str(config.pk)})

    assert response.status_code == 302
    assert response["Location"].endswith(url)
    assert calls["kwargs"] == {"reload": True, "remove": False, "pk": config.pk}


@pytest.mark.django_db
def test_generate_certificates_view_provisions_linked_certificate(monkeypatch, admin_client):
    certificate = SelfSignedCertificate.objects.create(
        name="linked-cert",
        domain="example.com",
        certificate_path="/tmp/example/fullchain.pem",
        certificate_key_path="/tmp/example/privkey.pem",
    )
    config = SiteConfiguration.objects.create(name="with-cert", protocol="https", certificate=certificate)

    calls: dict[str, str] = {}

    def fake_generate(self, *, sudo: str = "sudo"):
        calls["sudo"] = sudo
        return "generated"

    monkeypatch.setattr(SelfSignedCertificate, "generate", fake_generate)

    url = reverse("admin:nginx_siteconfiguration_generate_certificates") + f"?ids={config.pk}"
    response = admin_client.post(url, {"ids": str(config.pk)}, follow=True)

    assert response.status_code == 200
    assert calls["sudo"] == "sudo"
    messages = [str(message) for message in response.context["messages"]]
    assert any("generated" in message for message in messages)


@pytest.mark.django_db
def test_generate_certificates_view_creates_certificate_when_missing(monkeypatch, admin_client, settings):
    settings.ALLOWED_HOSTS = ["auto.example.com", "localhost", "testserver"]

    generated: dict[str, bool] = {}

    def fake_generate(self, *, sudo: str = "sudo"):
        generated["called"] = True
        return "auto-generated"

    monkeypatch.setattr(SelfSignedCertificate, "generate", fake_generate)

    config = SiteConfiguration.objects.create(name="missing-cert", protocol="https")

    url = reverse("admin:nginx_siteconfiguration_generate_certificates") + f"?ids={config.pk}"
    response = admin_client.post(url, {"ids": str(config.pk)}, follow=True)

    config.refresh_from_db()
    certificate = config.certificate
    assert certificate is not None
    assert isinstance(certificate._specific_certificate, SelfSignedCertificate)
    assert certificate.domain == "auto.example.com"
    assert generated["called"] is True

    messages = [str(message) for message in response.context["messages"]]
    assert any("auto-generated" in message for message in messages)


@pytest.mark.django_db
def test_generate_certificates_view_creates_certbot_certificate_when_selected(
    monkeypatch, admin_client, settings
):
    settings.ALLOWED_HOSTS = ["certbot.example.com", "testserver"]

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="godaddy-key",
        api_secret="godaddy-secret",
        default_domain="example.com",
        is_enabled=True,
    )

    requested: dict[str, object] = {}

    def fake_request(
        self,
        *,
        sudo: str = "sudo",
        validation_provider: str | None = None,
        dns_api_key: str | None = None,
        dns_api_secret: str | None = None,
        dns_propagation_seconds: int = 60,
    ):
        requested["sudo"] = sudo
        requested["validation_provider"] = validation_provider
        requested["dns_api_key"] = dns_api_key
        requested["dns_api_secret"] = dns_api_secret
        requested["dns_propagation_seconds"] = dns_propagation_seconds
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    config = SiteConfiguration.objects.create(name="missing-certbot", protocol="https")

    url = reverse("admin:nginx_siteconfiguration_generate_certificates") + f"?ids={config.pk}"
    response = admin_client.post(
        url,
        {
            "ids": str(config.pk),
            "certificate_type": "certbot",
            "validation_provider": "godaddy",
            "dns_propagation_seconds": "75",
        },
        follow=True,
    )

    config.refresh_from_db()
    certificate = config.certificate
    assert certificate is not None
    assert isinstance(certificate._specific_certificate, CertbotCertificate)
    assert certificate.domain == "certbot.example.com"
    assert requested["sudo"] == "sudo"
    assert requested["validation_provider"] == "godaddy"
    assert requested["dns_api_key"] == "godaddy-key"
    assert requested["dns_api_secret"] == "godaddy-secret"
    assert requested["dns_propagation_seconds"] == 75

    messages = [str(message) for message in response.context["messages"]]
    assert any("requested" in message for message in messages)


@pytest.mark.django_db
def test_default_certificate_domain_skips_cidr_and_ports(settings):
    from django.contrib.admin.sites import AdminSite

    from apps.nginx.admin import SiteConfigurationAdmin

    settings.ALLOWED_HOSTS = ["10.0.0.0/16", "admin.example.com:8443", "portal.example.com"]
    admin_view = SiteConfigurationAdmin(SiteConfiguration, AdminSite())

    assert admin_view._get_default_certificate_domain() == "admin.example.com"


@pytest.mark.django_db
def test_load_local_creates_site_configuration(admin_client, settings, tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()
    (lock_dir / "nginx_mode.lck").write_text("public", encoding="utf-8")
    (lock_dir / "role.lck").write_text("Control", encoding="utf-8")
    (lock_dir / "backend_port.lck").write_text("9001", encoding="utf-8")

    config_path = tmp_path / "arthexis.conf"
    config_path.write_text(
        """
        server {
            listen 80;
            listen [::]:80;
            server_name example.com;
            location / {
                proxy_pass http://127.0.0.1:9999/;
                proxy_set_header Connection $connection_upgrade;
            }
        }

        server {
            listen 443 ssl;
        }
        """,
        encoding="utf-8",
    )

    settings.BASE_DIR = base_dir
    settings.NGINX_SITE_PATH = str(config_path)

    url = reverse("admin:nginx_siteconfiguration_load_local")
    response = admin_client.post(url)

    assert response.status_code == 302
    config = SiteConfiguration.objects.get(name="example.com")
    assert config.mode == "public"
    assert config.role == "Control"
    assert config.port == 9999
    assert config.protocol == "https"
    assert config.include_ipv6 is True
    assert config.external_websockets is True


@pytest.mark.django_db
def test_preview_view_includes_dns_validation_provider_selector(admin_client):
    config = SiteConfiguration.objects.create(name="preview-fields")
    url = reverse("admin:nginx_siteconfiguration_preview") + f"?ids={config.pk}"

    response = admin_client.get(url)

    rendered = response.content.decode()
    assert "DNS validation provider" in rendered
    assert "dns_propagation_seconds" in rendered
