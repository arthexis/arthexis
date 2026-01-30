"""GitHub App models."""

from __future__ import annotations

import socket

from django.conf import settings
from django.contrib.sites.models import Site
from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.sigils.fields import SigilLongAutoField, SigilShortAutoField


class GitHubWebhook(Entity):
    """Base webhook configuration for GitHub integrations."""

    class ContentType(models.TextChoices):
        JSON = "json", _("JSON")
        FORM = "form", _("Form")

    name = models.CharField(max_length=255, blank=True)
    webhook_url = models.URLField(blank=True)
    webhook_slug = models.SlugField(
        max_length=255,
        blank=True,
        help_text=_(
            "Optional URL slug for the webhook endpoint. When provided, the full "
            "webhook URL is derived from the current site configuration."
        ),
    )
    webhook_secret = SigilShortAutoField(max_length=255, blank=True)
    webhook_events = models.JSONField(default=list, blank=True)
    webhook_active = models.BooleanField(default=True)
    webhook_content_type = models.CharField(
        max_length=20,
        choices=ContentType.choices,
        default=ContentType.JSON,
    )
    webhook_insecure_ssl = models.BooleanField(default=False)

    class Meta:
        abstract = True

    @classmethod
    def instance_base_url(cls) -> str:
        try:
            domain = Site.objects.get_current().domain.strip()
        except Site.DoesNotExist:
            domain = ""

        if not domain:
            fallback = getattr(settings, "DEFAULT_SITE_DOMAIN", "") or getattr(
                settings, "DEFAULT_DOMAIN", ""
            )
            if fallback:
                domain = fallback.strip()

        if not domain:
            for host in getattr(settings, "ALLOWED_HOSTS", []):
                if not isinstance(host, str):
                    continue
                host = host.strip()
                if not host or host.startswith("*") or "/" in host:
                    continue
                domain = host
                break

        if not domain:
            try:
                domain = socket.gethostname()
            except OSError:
                domain = ""
            domain = domain or "localhost"

        scheme = getattr(settings, "DEFAULT_HTTP_PROTOCOL", "http")
        return f"{scheme}://{domain}"

    @classmethod
    def webhook_path(cls, slug: str | None = None) -> str:
        try:
            if slug:
                return reverse("repos:github-webhook-app", kwargs={"app_slug": slug})
            return reverse("repos:github-webhook")
        except NoReverseMatch:  # pragma: no cover - defensive
            return "/repos/webhooks/github/"

    def webhook_full_url(self) -> str:
        if self.webhook_url:
            return self.webhook_url
        if self.webhook_slug:
            return f"{self.instance_base_url()}{self.webhook_path(self.webhook_slug)}"
        return f"{self.instance_base_url()}{self.webhook_path()}"

    def webhook_config(self) -> dict[str, object]:
        return {
            "url": self.webhook_full_url(),
            "secret": self.webhook_secret,
            "content_type": self.webhook_content_type,
            "insecure_ssl": "1" if self.webhook_insecure_ssl else "0",
            "active": self.webhook_active,
            "events": list(self.webhook_events or ()),
        }


class GitHubApp(GitHubWebhook):
    """Represents a GitHub App configuration and credentials."""

    class AuthMethod(models.TextChoices):
        APP = "app", _("GitHub App")
        USER_TOKEN = "user_token", _("User Token")
        USER_PASSWORD = "user_password", _("User Password")
        TEMP_PASSWORD = "temp_password", _("Temporary Password")

    display_name = models.CharField(max_length=255)
    app_id = models.PositiveBigIntegerField(unique=True)
    app_slug = models.SlugField(max_length=255, blank=True)
    app_url = models.URLField(blank=True)
    homepage_url = models.URLField(blank=True)
    callback_url = models.URLField(blank=True)
    setup_url = models.URLField(blank=True)
    redirect_url = models.URLField(blank=True)
    client_id = SigilShortAutoField(max_length=255, blank=True)
    client_secret = SigilShortAutoField(max_length=255, blank=True)
    private_key = SigilLongAutoField(blank=True)
    public_key = SigilLongAutoField(blank=True)
    permissions = models.JSONField(default=dict, blank=True)
    default_events = models.JSONField(default=list, blank=True)
    auth_method = models.CharField(
        max_length=20,
        choices=AuthMethod.choices,
        default=AuthMethod.APP,
    )
    auth_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="github_apps",
        help_text=_(
            "User to authenticate as for non-admin GitHub access or delegated "
            "operations."
        ),
    )
    auth_token = SigilShortAutoField(
        max_length=255,
        blank=True,
        help_text=_("Personal access token or OAuth token for the auth user."),
    )
    auth_password = SigilShortAutoField(
        max_length=255,
        blank=True,
        help_text=_(
            "Password for the auth user. Temporary passwords are permitted for the "
            "system account when configured."
        ),
    )

    class Meta:
        verbose_name = _("GitHub App")
        verbose_name_plural = _("GitHub Apps")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display_name


class GitHubAppInstall(Entity):
    """Tracks installations of a GitHub App."""

    app = models.ForeignKey(
        GitHubApp,
        on_delete=models.CASCADE,
        related_name="installs",
    )
    installation_id = models.PositiveBigIntegerField()
    account_id = models.PositiveBigIntegerField(null=True, blank=True)
    account_login = models.CharField(max_length=255, blank=True)
    target_type = models.CharField(max_length=50, blank=True)
    repository_selection = models.CharField(max_length=50, blank=True)
    permissions = models.JSONField(default=dict, blank=True)
    events = models.JSONField(default=list, blank=True)
    installed_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    app_version = models.CharField(max_length=50, blank=True)
    app_revision = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = _("GitHub App Install")
        verbose_name_plural = _("GitHub App Installs")
        constraints = [
            models.UniqueConstraint(
                fields=["app", "installation_id"],
                name="unique_github_app_install",
            )
        ]
        indexes = [
            models.Index(fields=["installation_id"], name="github_install_id_idx"),
            models.Index(fields=["account_login"], name="github_install_login_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        identity = self.account_login or "unknown"
        return f"{self.app} install for {identity}".strip()
