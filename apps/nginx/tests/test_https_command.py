"""Tests for the nginx https management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.certs.models import CertbotCertificate, CertificateBase
from apps.certs.services import CertbotChallengeError, CertificateVerificationResult
from apps.nginx import services
from apps.nginx.models import SiteConfiguration


@pytest.fixture(autouse=True)
def _stub_certbot_availability(monkeypatch):
    """Keep command tests independent from host-level certbot installation."""

    from apps.nginx.management.commands.https_parts import certificate_flow

    monkeypatch.setattr(
        certificate_flow, "ensure_certbot_available", lambda *, sudo="sudo": None
    )


@pytest.mark.django_db
def test_https_enable_with_godaddy_sets_dns_challenge(monkeypatch):
    """`https --enable --godaddy` should configure a certbot DNS challenge certificate."""

    provision_calls: dict[str, str] = {}

    def fake_provision(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        provision_calls["sudo"] = sudo
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    from apps.nginx.management.commands.https_parts import certificate_flow

    monkeypatch.setattr(
        certificate_flow, "_validate_godaddy_setup", lambda service, certificate: None
    )

    call_command("https", "--enable", "--godaddy", "example.com", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.com")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.GODADDY
    assert provision_calls["sudo"] == ""


@pytest.mark.django_db
def test_https_enable_with_godaddy_requires_dns_credential(monkeypatch):
    """GoDaddy mode should stop with guidance if no credential is configured."""

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    with pytest.raises(CommandError, match="requires credentials"):
        call_command("https", "--enable", "--godaddy", "example.com")


@pytest.mark.django_db
def test_https_godaddy_implies_enable(monkeypatch):
    """`https --godaddy` should implicitly behave like `https --enable --godaddy`."""

    provision_calls: dict[str, str] = {}

    def fake_provision(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        provision_calls["sudo"] = sudo
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    from apps.nginx.management.commands.https_parts import certificate_flow

    monkeypatch.setattr(
        certificate_flow, "_validate_godaddy_setup", lambda service, certificate: None
    )

    call_command("https", "--godaddy", "example.net", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.net")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.GODADDY
    assert provision_calls["sudo"] == ""


@pytest.mark.django_db
def test_https_certbot_implies_enable(monkeypatch):
    """`https --certbot` should implicitly behave like `https --enable --certbot`."""

    provision_calls: dict[str, str] = {}

    def fake_provision(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        provision_calls["sudo"] = sudo
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--certbot", "example.net", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.net")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.NGINX
    assert provision_calls["sudo"] == ""


@pytest.mark.django_db
def test_https_site_uses_latest_enabled_config_port(monkeypatch):
    """New HTTPS site configs should inherit the latest enabled nginx port when available."""

    from django.utils import timezone

    SiteConfiguration.objects.create(
        name="active-upstream",
        enabled=True,
        mode="public",
        protocol="http",
        port=9999,
        last_applied_at=timezone.now(),
    )
    SiteConfiguration.objects.create(
        name="unapplied-upstream",
        enabled=True,
        mode="public",
        protocol="http",
        port=7777,
        last_applied_at=None,
    )

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--site", "porsche-abb-1.gelectriic.com", "--no-sudo")

    config = SiteConfiguration.objects.get(name="porsche-abb-1.gelectriic.com")
    assert config.port == 9999


@pytest.mark.django_db
def test_https_site_url_implies_enable_and_creates_managed_site(monkeypatch):
    """`https --site wss://...` should normalize host and stage managed site metadata."""

    provision_calls: dict[str, str] = {}

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        provision_calls["sudo"] = sudo
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--site", "wss://porsche-abb-1.gelectriic.com/", "--no-sudo")

    config = SiteConfiguration.objects.get(name="porsche-abb-1.gelectriic.com")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.NGINX
    assert provision_calls["sudo"] == ""

    from django.contrib.sites.models import Site

    site = Site.objects.get(domain="porsche-abb-1.gelectriic.com")
    assert getattr(site, "managed", False) is True
    assert getattr(site, "require_https", False) is True


@pytest.mark.django_db
def test_https_disable_clears_managed_site_require_https(monkeypatch):
    """`https --disable` should clear managed Site HTTPS requirement."""

    from django.contrib.sites.models import Site

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--site", "example.com", "--no-sudo")

    site = Site.objects.get(domain="example.com")
    assert getattr(site, "require_https", False) is True

    call_command("https", "--disable", "--certbot", "example.com")

    site.refresh_from_db()
    assert getattr(site, "require_https", True) is False


@pytest.mark.django_db
def test_https_site_rejects_invalid_hostname_characters():
    """`--site` should reject invalid hostnames that could break nginx config rendering."""

    with pytest.raises(CommandError, match="valid hostname or URL"):
        call_command("https", "--site", "[example.com; return 301 http://evil.com;]")


@pytest.mark.django_db
def test_https_site_rejects_loopback_host():
    """`--site` should reject localhost/loopback targets and direct users to --local."""

    with pytest.raises(CommandError, match="public host"):
        call_command("https", "--site", "localhost")

    with pytest.raises(CommandError, match="public host"):
        call_command("https", "--site", "127.0.0.1")

    with pytest.raises(CommandError, match="public host"):
        call_command("https", "--site", "http://[::1]")


@pytest.mark.django_db
def test_https_site_rejects_local_combination():
    """`--site` and `--local` should fail to avoid contradictory certificate intent."""

    with pytest.raises(CommandError, match="cannot be combined"):
        call_command("https", "--enable", "--local", "--site", "example.com")


@pytest.mark.django_db
def test_prompt_for_godaddy_credential_allows_redirected_stdout(monkeypatch):
    """Credential prompts should still run when stdout is redirected but stdin is interactive."""

    import sys

    from apps.dns.models import DNSProviderCredential
    from apps.nginx.management.commands.https import Command
    from apps.nginx.management.commands.https_parts.certificate_flow import (
        _prompt_for_godaddy_credential,
    )
    from apps.nginx.management.commands.https_parts.service import (
        HttpsProvisioningService,
    )

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    prompt_map = {
        "Enter credentials now and save to DNS Credentials? [y/N]: ": "y",
        "GoDaddy customer ID (optional): ": "customer-42",
        "Use GoDaddy OTE sandbox environment? [y/N]: ": "n",
    }
    monkeypatch.setattr("builtins.input", lambda prompt="": prompt_map[prompt])
    getpass_values = iter(["key-123", "secret-456"])
    monkeypatch.setattr(
        "apps.nginx.management.commands.https_parts.certificate_flow.getpass",
        lambda _prompt="": next(getpass_values),
    )

    command = Command()
    service = HttpsProvisioningService(command)
    credential = _prompt_for_godaddy_credential(service, "example.edu")

    assert credential is not None
    assert credential.provider == DNSProviderCredential.Provider.GODADDY
    assert credential.api_key == "key-123"
    assert credential.api_secret == "secret-456"
    assert credential.customer_id == "customer-42"
    assert credential.default_domain == "example.edu"
    assert credential.is_enabled is True
    assert credential.use_sandbox is False


@pytest.mark.django_db
def test_https_enable_restores_https_when_http01_challenge_fails(monkeypatch):
    """HTTP-01 failures should re-apply HTTPS after temporary bootstrap downgrade."""

    apply_protocols: list[str] = []

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        raise CertbotChallengeError("Some challenges have failed.")

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        apply_protocols.append(self.protocol)
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    with pytest.raises(CommandError, match="HTTPS enable did not complete"):
        call_command("https", "--enable", "--certbot", "example.com", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.com")
    assert config.protocol == "https"
    assert apply_protocols == ["http", "https"]


@pytest.mark.django_db
def test_https_enable_http01_bootstraps_http_site_before_certbot(monkeypatch):
    """HTTP-01 certbot runs should bootstrap nginx HTTP config before certificate issuance."""

    apply_protocols: list[str] = []

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        apply_protocols.append(self.protocol)
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--enable", "--certbot", "example.com", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.com")
    assert config.protocol == "https"
    assert apply_protocols == ["http", "https"]


@pytest.mark.django_db
def test_https_renew_reapplies_https_configuration_for_renewed_certificate(monkeypatch):
    """`--renew` should reapply nginx for HTTPS sites using renewed certificates."""

    from datetime import timedelta

    from django.utils import timezone

    cert = CertbotCertificate.objects.create(
        name="reapply-example-com-certbot",
        domain="reapply.example.com",
        certificate_path="/etc/letsencrypt/live/reapply.example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/reapply.example.com/privkey.pem",
        expiration_date=timezone.now() - timedelta(hours=2),
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )
    SiteConfiguration.objects.create(
        name="reapply.example.com",
        enabled=True,
        protocol="https",
        certificate=cert,
    )

    monkeypatch.setattr(
        CertificateBase,
        "update_expiration_date",
        lambda self, *, sudo="sudo": self.expiration_date,
    )

    def fake_renew(self, *, sudo="sudo"):
        self.expiration_date = timezone.now() + timedelta(days=90)
        self.save(update_fields=["expiration_date", "updated_at"])

    monkeypatch.setattr(CertificateBase, "renew", fake_renew)

    applied_calls: list[tuple[str, bool]] = []

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        applied_calls.append((self.name, reload))
        return services.ApplyResult(
            changed=True,
            validated=True,
            reloaded=reload,
            message=f"applied:{self.name}",
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command(
        "https",
        "--renew",
        "--godaddy",
        "reapply.example.com",
        "--no-sudo",
        stdout=out,
    )

    assert applied_calls == [("reapply.example.com", True)]
    rendered = out.getvalue()
    assert "Renewed certificate: domain=reapply.example.com;" in rendered
    assert "Reloaded HTTPS site configuration(s): reapply.example.com." in rendered


@pytest.mark.django_db
def test_https_renew_reapplies_https_configuration_without_reload_when_requested(
    monkeypatch,
):
    """`--renew --no-reload` should reapply HTTPS sites without triggering reload."""

    from datetime import timedelta

    from django.utils import timezone

    cert = CertbotCertificate.objects.create(
        name="reapply-no-reload-example-com-certbot",
        domain="reapply-no-reload.example.com",
        certificate_path="/etc/letsencrypt/live/reapply-no-reload.example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/reapply-no-reload.example.com/privkey.pem",
        expiration_date=timezone.now() - timedelta(hours=2),
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )
    SiteConfiguration.objects.create(
        name="reapply-no-reload.example.com",
        enabled=True,
        protocol="https",
        certificate=cert,
    )

    monkeypatch.setattr(
        CertificateBase,
        "update_expiration_date",
        lambda self, *, sudo="sudo": self.expiration_date,
    )

    def fake_renew(self, *, sudo="sudo"):
        self.expiration_date = timezone.now() + timedelta(days=90)
        self.save(update_fields=["expiration_date", "updated_at"])

    monkeypatch.setattr(CertificateBase, "renew", fake_renew)

    applied_calls: list[tuple[str, bool]] = []

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        applied_calls.append((self.name, reload))
        return services.ApplyResult(
            changed=True,
            validated=True,
            reloaded=reload,
            message=f"applied:{self.name}",
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command(
        "https",
        "--renew",
        "--godaddy",
        "reapply-no-reload.example.com",
        "--no-sudo",
        "--no-reload",
        stdout=out,
    )

    assert applied_calls == [("reapply-no-reload.example.com", False)]
    rendered = out.getvalue()
    assert (
        "Applied without reload HTTPS site configuration(s): reapply-no-reload.example.com."
        in rendered
    )


@pytest.mark.django_db
def test_https_migrate_from_updates_site_and_node_records(monkeypatch):
    """`--migrate-from` should move existing node/site references to the new domain."""

    from django.contrib.sites.models import Site

    from apps.nodes.models import Node

    previous_site = Site.objects.create(domain="arthexis.com", name="arthexis.com")
    Node.objects.create(
        hostname="arthexis.com",
        mac_address="00:11:22:33:44:55",
        base_site=previous_site,
    )

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command(
        "https",
        "--site",
        "arthexis.gelectriic.com",
        "--migrate-from",
        "arthexis.com",
        "--no-sudo",
    )

    migrated_site = Site.objects.get(domain="arthexis.gelectriic.com")
    node = Node.objects.get(mac_address="00:11:22:33:44:55")
    assert node.base_site_id == migrated_site.pk
    assert node.hostname == "arthexis.gelectriic.com"


@pytest.mark.django_db
def test_https_migrate_from_requires_public_target_domain():
    """`--migrate-from` should reject local-only migrations without public target hosts."""

    with pytest.raises(CommandError, match="requires a target domain"):
        call_command("https", "--enable", "--local", "--migrate-from", "arthexis.com")


@pytest.mark.django_db
def test_https_migrate_from_copies_site_configuration(monkeypatch):
    """Migrated domains should inherit existing site configuration defaults when possible."""

    from django.utils import timezone

    source = SiteConfiguration.objects.create(
        name="arthexis.com",
        enabled=True,
        mode="public",
        role="default",
        protocol="http",
        port=9443,
        managed_subdomains="admin,api,status",
        include_ipv6=True,
        last_applied_at=timezone.now(),
    )

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command(
        "https",
        "--site",
        "arthexis.gelectriic.com",
        "--migrate-from",
        source.name,
        "--no-sudo",
    )

    target = SiteConfiguration.objects.get(name="arthexis.gelectriic.com")
    assert target.mode == "public"
    assert target.role == "default"
    assert target.port == 9443
    assert target.managed_subdomains == "admin,api,status"
    assert target.include_ipv6 is True


@pytest.mark.django_db
def test_https_migrate_from_disables_source_https_config(monkeypatch):
    """Source HTTPS configuration should be deactivated after migration."""

    from django.utils import timezone

    source = SiteConfiguration.objects.create(
        name="arthexis.com",
        enabled=True,
        mode="public",
        protocol="https",
        port=443,
        managed_subdomains="admin,api,status",
        include_ipv6=True,
        last_applied_at=timezone.now(),
    )

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command(
        "https",
        "--site",
        "arthexis.gelectriic.com",
        "--migrate-from",
        source.name,
        "--no-sudo",
    )

    source.refresh_from_db()
    assert source.enabled is False


@pytest.mark.django_db
def test_https_enable_passes_force_renewal_to_certbot(monkeypatch):
    """`https --force-renewal` should forward the flag to certbot provisioning."""

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
    )

    provision_calls: dict[str, object] = {}

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        provision_calls["force_renewal"] = force_renewal
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--enable", "--godaddy", "example.dev", "--force-renewal")

    assert provision_calls["force_renewal"] is True


@pytest.mark.django_db
def test_https_validate_reports_detailed_certificate_status(monkeypatch):
    """`https --validate` should print success details for verified certbot certificates."""

    from datetime import timedelta

    from django.utils import timezone

    cert = CertbotCertificate.objects.create(
        name="validate-example-com-certbot",
        domain="validate.example.com",
        certificate_path="/etc/letsencrypt/live/validate.example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/validate.example.com/privkey.pem",
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
        expiration_date=timezone.now() + timedelta(days=10),
    )

    SiteConfiguration.objects.create(
        name="validate.example.com",
        enabled=True,
        protocol="https",
        certificate=cert,
    )

    monkeypatch.setattr(
        CertbotCertificate,
        "verify_paths",
        lambda self, *, sudo="sudo": CertificateVerificationResult(
            ok=True,
            messages=["all good"],
        ),
    )

    out = StringIO()
    call_command("https", "--validate", stdout=out)

    rendered = out.getvalue()
    assert "validate.example.com" in rendered
    assert "all good" in rendered


@pytest.mark.django_db
def test_https_renew_domain_filter_reports_targeted_noop_message(monkeypatch):
    """`https --renew <domain>` should report when the target cert is not due yet."""

    from datetime import timedelta

    from django.utils import timezone

    cert = CertbotCertificate.objects.create(
        name="filter-example-com-certbot",
        domain="filter.example.com",
        certificate_path="/etc/letsencrypt/live/filter.example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/filter.example.com/privkey.pem",
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
        expiration_date=timezone.now() + timedelta(days=45),
    )
    SiteConfiguration.objects.create(
        name="filter.example.com",
        enabled=True,
        protocol="https",
        certificate=cert,
    )

    monkeypatch.setattr(
        CertificateBase,
        "update_expiration_date",
        lambda self, *, sudo="sudo": self.expiration_date,
    )

    out = StringIO()
    call_command("https", "--renew", "filter.example.com", stdout=out)

    rendered = out.getvalue()
    assert "not due for renewal" in rendered
    assert "filter.example.com" in rendered
