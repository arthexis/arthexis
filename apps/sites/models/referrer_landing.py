from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.contrib.sites.models import Site
from django.db import models
from django.http.request import split_domain_port
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity

logger = logging.getLogger(__name__)


class ReferrerLandingManager(models.Manager):
    def match_for_site(self, site: Site, referer: str) -> "ReferrerLanding | None":
        domain = extract_referrer_domain(referer)
        if not domain or site is None:
            return None

        candidates = list(
            self.filter(site=site, enabled=True, is_deleted=False).select_related(
                "landing"
            )
        )
        candidates.sort(key=lambda item: len(item.referrer_domain or ""), reverse=True)
        for candidate in candidates:
            if candidate.matches_domain(domain):
                return candidate
        return None


def extract_referrer_domain(referer: str) -> str:
    if not referer:
        return ""

    referer = referer.strip()
    if not referer:
        return ""

    host = ""
    try:
        parsed = urlsplit(referer)
    except ValueError:  # pragma: no cover - malformed input
        logger.debug("Unable to parse referer: %s", referer, exc_info=True)
        return ""

    if parsed.netloc:
        host = parsed.netloc
    elif parsed.path and "://" not in referer:
        host = parsed.path.split("/")[0]

    if not host:
        return ""

    host, _ = split_domain_port(host)
    return normalize_domain(host)


def normalize_domain(value: str) -> str:
    domain = (value or "").strip().lower()
    if domain.endswith("."):
        domain = domain[:-1]
    return domain


class ReferrerLanding(Entity):
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="referrer_landings"
    )
    referrer_domain = models.CharField(
        max_length=255,
        help_text=_("Referrer domain to match, such as example.com"),
    )
    landing = models.ForeignKey(
        "Landing",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referrer_landings",
    )
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    objects = ReferrerLandingManager()

    class Meta:
        unique_together = ("site", "referrer_domain")
        verbose_name = _("Referrer Landing")
        verbose_name_plural = _("Referrer Landings")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.referrer_domain} -> {self.landing or 'README'}"

    def matches_domain(self, domain: str) -> bool:
        domain = normalize_domain(domain)
        referrer_domain = normalize_domain(self.referrer_domain)
        if not domain or not referrer_domain:
            return False
        if domain == referrer_domain:
            return True
        return domain.endswith(f".{referrer_domain}")

    def save(self, *args, **kwargs):
        self.referrer_domain = extract_referrer_domain(self.referrer_domain)
        super().save(*args, **kwargs)
