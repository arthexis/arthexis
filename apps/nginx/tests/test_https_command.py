"""Tests for the nginx https management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.certs.models import CertificateBase, CertbotCertificate
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
def test_https_enable_with_godaddy_reports_manual_steps(monkeypatch):
    """GoDaddy mode should print setup guidance for administrators."""

    from apps.dns.models import DNSProviderCredential

    credential = DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
    )

    def fake_provision(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command("https", "--enable", "--godaddy", "example.org", stdout=out)

    cert = SiteConfiguration.objects.get(
        name="example.org"
    ).certificate._specific_certificate
    assert cert.dns_credential_id == credential.id
    rendered = out.getvalue()
    assert "Ensure certbot and Python requests are available" in rendered


@pytest.mark.django_db
def test_https_enable_with_godaddy_sandbox_override(monkeypatch):
    """`https --godaddy --sandbox` should override credential sandbox mode per run."""

    from apps.dns.models import DNSProviderCredential

    credential = DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
        use_sandbox=False,
    )

    provision_calls: dict[str, object] = {}

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        provision_calls["sudo"] = sudo
        provision_calls["dns_use_sandbox"] = dns_use_sandbox
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command(
        "https",
        "--enable",
        "--godaddy",
        "example.org",
        "--sandbox",
        stdout=out,
    )

    cert = SiteConfiguration.objects.get(
        name="example.org"
    ).certificate._specific_certificate
    assert cert.dns_credential_id == credential.id
    assert provision_calls["dns_use_sandbox"] is True


@pytest.mark.django_db
def test_https_enable_with_godaddy_no_sandbox_override(monkeypatch):
    """`https --godaddy --no-sandbox` should force production DNS API for this run."""

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
        use_sandbox=True,
    )

    provision_calls: dict[str, object] = {}

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        provision_calls["dns_use_sandbox"] = dns_use_sandbox
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--enable", "--godaddy", "example.net", "--no-sandbox")

    assert provision_calls["dns_use_sandbox"] is False


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
def test_https_enable_force_renewal_warns_when_paths_change(monkeypatch):
    """`--force-renewal` should include old/new cert and key paths when lineage shifts."""

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
    )

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        self.certificate_path = "/etc/letsencrypt/live/example.dev-0001/fullchain.pem"
        self.certificate_key_path = "/etc/letsencrypt/live/example.dev-0001/privkey.pem"
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command(
        "https", "--enable", "--godaddy", "example.dev", "--force-renewal", stdout=out
    )

    rendered = out.getvalue()
    assert "cert old=/etc/letsencrypt/live/example.dev/fullchain.pem" in rendered
    assert "new=/etc/letsencrypt/live/example.dev-0001/fullchain.pem" in rendered
    assert "key old=/etc/letsencrypt/live/example.dev/privkey.pem" in rendered
    assert "new=/etc/letsencrypt/live/example.dev-0001/privkey.pem" in rendered


@pytest.mark.django_db
def test_https_enable_force_renewal_preserves_existing_lineage_paths(monkeypatch):
    """Regression: command must not reset lineage paths before force-renewal validation."""

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
    )

    cert_name = "example.dev-example-dev-certbot"
    CertbotCertificate.objects.create(
        name=cert_name,
        domain="example.dev",
        certificate_path="/etc/letsencrypt/live/example.dev-0001/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/example.dev-0001/privkey.pem",
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )

    observed_paths: dict[str, str] = {}

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        observed_paths["before"] = self.certificate_path
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--enable", "--godaddy", "example.dev", "--force-renewal")

    assert (
        observed_paths["before"]
        == "/etc/letsencrypt/live/example.dev-0001/fullchain.pem"
    )


@pytest.mark.django_db
def test_https_enable_force_renewal_raises_if_certificate_stays_expired(monkeypatch):
    """Regression: --force-renewal must fail when certbot leaves an expired cert in place."""

    from datetime import timedelta
    from django.utils import timezone

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
    )

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        self.expiration_date = timezone.now() - timedelta(minutes=1)
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    with pytest.raises(CommandError, match="still expired"):
        call_command("https", "--enable", "--godaddy", "example.dev", "--force-renewal")


@pytest.mark.django_db
def test_https_enable_force_renewal_warns_when_expiration_unavailable(monkeypatch):
    """`--force-renewal` should warn when certbot cannot report expiration metadata."""

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
    )

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        self.expiration_date = None
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command(
        "https", "--enable", "--godaddy", "example.dev", "--force-renewal", stdout=out
    )

    rendered = out.getvalue()
    assert "expiration could not be determined" in rendered
    assert "services.get_certificate_expiration" in rendered


@pytest.mark.django_db
def test_https_enable_warns_when_certificate_is_expired(monkeypatch):
    """`https --enable` should warn with remediation when the issued cert is already expired."""

    from datetime import timedelta
    from django.utils import timezone

    def fake_provision(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        self.expiration_date = timezone.now() - timedelta(days=1)
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command(
        "https", "--enable", "--certbot", "example.warn", "--no-sudo", stdout=out
    )

    rendered = out.getvalue()
    assert "has expired" in rendered
    assert "./command.sh https --renew" in rendered
    assert "only when you need to force immediate reissuance" in rendered
    assert "--force-renewal" in rendered


@pytest.mark.django_db
def test_https_enable_warns_when_self_signed_certificate_is_expired(monkeypatch):
    """Expired self-signed cert guidance should not suggest certbot-specific force renewal."""

    from datetime import timedelta
    from django.utils import timezone

    from apps.certs.models import SelfSignedCertificate

    def fake_provision(self, *, sudo: str = "sudo", subject_alt_names=None):
        self.expiration_date = timezone.now() - timedelta(days=1)
        return "generated"

    monkeypatch.setattr(SelfSignedCertificate, "generate", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command("https", "--enable", "--local", "--no-sudo", stdout=out)

    rendered = out.getvalue()
    assert "has expired" in rendered
    assert "./command.sh https --renew" in rendered
    assert "--force-renewal" not in rendered


@pytest.mark.django_db
def test_https_warn_days_must_be_non_negative():
    """`--warn-days` should reject negative values with a clear validation error."""

    with pytest.raises(CommandError, match="positive integer"):
        call_command(
            "https", "--enable", "--certbot", "example.com", "--warn-days", "-1"
        )


@pytest.mark.django_db
def test_https_enable_surfaces_certbot_challenge_as_command_error(monkeypatch):
    """Regression: certbot challenge failures should be shown as user-facing command output."""

    def fake_request(
        self, *, sudo: str = "sudo", dns_use_sandbox=None, force_renewal: bool = False
    ):
        raise CertbotChallengeError("Some challenges have failed.")

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    with pytest.raises(CommandError, match="HTTPS enable did not complete"):
        call_command("https", "--enable", "--certbot", "example.com", "--no-sudo")


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
def test_https_enable_rejects_option_like_domain_for_certbot():
    """`--certbot` should validate domain and reject values that parse as CLI flags."""

    with pytest.raises(CommandError, match="valid hostname"):
        call_command("https", "--enable", "--certbot=--help")


@pytest.mark.django_db
def test_https_enable_rejects_option_like_domain_for_godaddy():
    """`--godaddy` should validate domain and reject values that parse as CLI flags."""

    with pytest.raises(CommandError, match="valid hostname"):
        call_command("https", "--enable", "--godaddy=--help")


@pytest.mark.django_db
def test_https_renew_refreshes_expiration_before_due_check(monkeypatch):
    """Regression: `--renew` should refresh expiration metadata before deciding due certificates."""

    from datetime import timedelta
    from django.utils import timezone

    cert = CertbotCertificate.objects.create(
        name="example.com-example-com-certbot",
        domain="example.com",
        certificate_path="/etc/letsencrypt/live/example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/example.com/privkey.pem",
        expiration_date=timezone.now() + timedelta(days=30),
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )

    def fake_update_expiration_date(self, *, sudo: str = "sudo"):
        self.expiration_date = timezone.now() - timedelta(hours=1)
        return self.expiration_date

    renewed_ids: list[int] = []

    def fake_renew(self, *, sudo: str = "sudo"):
        renewed_ids.append(self.pk)
        return "renewed"

    monkeypatch.setattr(
        CertificateBase,
        "update_expiration_date",
        fake_update_expiration_date,
    )
    monkeypatch.setattr(CertificateBase, "renew", fake_renew)

    out = StringIO()
    call_command(
        "https", "--renew", "--godaddy", "example.com", "--no-sudo", stdout=out
    )

    assert renewed_ids == [cert.pk]
    assert "Renewed 1 certificate(s)." in out.getvalue()


@pytest.mark.django_db
def test_https_renew_keeps_missing_certificate_files_due(monkeypatch):
    """`--renew` should still renew due certs when their on-disk certificate file is missing."""

    from datetime import timedelta
    from django.utils import timezone

    cert = CertbotCertificate.objects.create(
        name="missing-file-example-com-certbot",
        domain="missing-file.example.com",
        certificate_path="/definitely/missing/fullchain.pem",
        certificate_key_path="/definitely/missing/privkey.pem",
        expiration_date=timezone.now() - timedelta(days=1),
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )

    def fake_update_expiration_date(self, *, sudo: str = "sudo"):
        self.expiration_date = None
        return None

    renewed_ids: list[int] = []

    def fake_renew(self, *, sudo: str = "sudo"):
        renewed_ids.append(self.pk)
        return "renewed"

    monkeypatch.setattr(
        CertificateBase, "update_expiration_date", fake_update_expiration_date
    )
    monkeypatch.setattr(CertificateBase, "renew", fake_renew)

    out = StringIO()
    call_command(
        "https",
        "--renew",
        "--godaddy",
        "missing-file.example.com",
        "--no-sudo",
        stdout=out,
    )

    assert renewed_ids == [cert.pk]
    assert "Renewed 1 certificate(s)." in out.getvalue()


@pytest.mark.django_db
def test_https_renew_domain_filter_reports_targeted_noop_message(monkeypatch):
    """`--renew --godaddy <domain>` should emit a domain-scoped no-op message when nothing is due."""

    from datetime import timedelta
    from django.utils import timezone

    CertbotCertificate.objects.create(
        name="example.com-example-com-certbot",
        domain="example.com",
        certificate_path="/etc/letsencrypt/live/example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/example.com/privkey.pem",
        expiration_date=timezone.now() + timedelta(days=30),
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )

    monkeypatch.setattr(
        CertificateBase,
        "update_expiration_date",
        lambda self, *, sudo="sudo": self.expiration_date,
    )

    out = StringIO()
    call_command(
        "https", "--renew", "--godaddy", "example.com", "--no-sudo", stdout=out
    )

    rendered = out.getvalue()
    assert "No certificates were due for renewal for example.com." in rendered
    assert "--force-renewal --certbot example.com" in rendered


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
def test_https_validate_reports_detailed_certificate_status(monkeypatch):
    """`--validate` should report certificate verification, expiration, and filesystem paths."""

    from datetime import timedelta
    from django.utils import timezone

    cert = CertbotCertificate.objects.create(
        name="validate-example-com-certbot",
        domain="validate.example.com",
        certificate_path="/etc/letsencrypt/live/validate.example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/validate.example.com/privkey.pem",
        expiration_date=timezone.now() + timedelta(days=20),
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )
    SiteConfiguration.objects.create(
        name="validate.example.com",
        enabled=True,
        protocol="https",
        certificate=cert,
    )

    monkeypatch.setattr(
        CertificateBase,
        "verify",
        lambda self, *, sudo="sudo": CertificateVerificationResult(
            ok=True,
            messages=["Certificate chain verified."],
        ),
    )

    out = StringIO()
    call_command(
        "https",
        "--validate",
        "--godaddy",
        "validate.example.com",
        "--no-sudo",
        stdout=out,
    )

    rendered = out.getvalue()
    assert "HTTPS status report:" in rendered
    assert "validate.example.com: protocol=https, enabled=True" in rendered
    assert "Certificate status: valid." in rendered
    assert "Certificate chain verified." in rendered
    assert "Expiration:" in rendered
    assert (
        "Paths: cert=/etc/letsencrypt/live/validate.example.com/fullchain.pem; key=/etc/letsencrypt/live/validate.example.com/privkey.pem."
        in rendered
    )


@pytest.mark.django_db
def test_https_renew_domain_filter_reports_existing_certificate_details_when_not_due(
    monkeypatch,
):
    """`--renew` no-op output should include existing certificate details for operator visibility."""

    from datetime import timedelta
    from django.utils import timezone

    CertbotCertificate.objects.create(
        name="existing-example-com-certbot",
        domain="existing.example.com",
        certificate_path="/etc/letsencrypt/live/existing.example.com/fullchain.pem",
        certificate_key_path="/etc/letsencrypt/live/existing.example.com/privkey.pem",
        expiration_date=timezone.now() + timedelta(days=30),
        challenge_type=CertbotCertificate.ChallengeType.GODADDY,
    )

    monkeypatch.setattr(
        CertificateBase,
        "update_expiration_date",
        lambda self, *, sudo="sudo": self.expiration_date,
    )

    out = StringIO()
    call_command(
        "https",
        "--renew",
        "--godaddy",
        "existing.example.com",
        "--no-sudo",
        stdout=out,
    )

    rendered = out.getvalue()
    assert "No certificates were due for renewal for existing.example.com." in rendered
    assert "Tracked certificate status:" in rendered
    assert "domain=existing.example.com;" in rendered
    assert "source=certbot (godaddy dns-01);" in rendered
    assert "status=valid" in rendered


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
def test_https_migrate_from_rejects_non_enable_actions():
    """`--migrate-from` should fail fast when combined with non-enable actions."""

    with pytest.raises(CommandError, match="only supported when enabling HTTPS"):
        call_command(
            "https",
            "--disable",
            "--site",
            "example.com",
            "--migrate-from",
            "old.example.com",
        )


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
