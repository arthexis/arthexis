from __future__ import annotations

import logging
from dataclasses import dataclass
import re
from typing import Any

import bleach
from django.db import models
from django.utils.html import mark_safe
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity

logger = logging.getLogger(__name__)

DEFAULT_WIKIMEDIA_ENDPOINT = "https://en.wikipedia.org/w/api.php"
DEFAULT_USER_AGENT = "ArthexisAdminWikiHelper/1.0 (+https://arthexis.com)"


class WikimediaBridge(Entity):
    """Connection details for reaching a specific Wikimedia instance."""

    slug = models.SlugField(
        _("Slug"), max_length=100, unique=True, help_text=_("Identifier for this wiki."),
    )
    name = models.CharField(_("Name"), max_length=100)
    api_endpoint = models.URLField(
        _("API endpoint"),
        default=DEFAULT_WIKIMEDIA_ENDPOINT,
        help_text=_("Base Wikimedia API endpoint (usually https://<domain>/w/api.php)."),
    )
    language_code = models.CharField(
        _("Language code"),
        max_length=8,
        default="en",
        help_text=_("Language for requests against the wiki."),
    )
    user_agent = models.CharField(
        _("User agent"),
        max_length=255,
        default=DEFAULT_USER_AGENT,
        help_text=_("User agent sent with Wikimedia requests."),
    )
    timeout = models.PositiveIntegerField(
        _("Timeout (seconds)"),
        default=5,
        help_text=_("Maximum time to wait for Wikimedia responses."),
    )
    cache_timeout = models.PositiveIntegerField(
        _("Cache timeout (seconds)"),
        default=60 * 60 * 12,
        help_text=_("How long to cache fetched wiki entries."),
    )

    class Meta:
        verbose_name = _("Wikimedia bridge")
        verbose_name_plural = _("Wikimedia bridges")
        db_table = "wikis_wikimedia_bridge"
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @classmethod
    def get_default(cls) -> "WikimediaBridge | None":
        bridge = cls.objects.filter(is_deleted=False).first()
        if bridge:
            return bridge
        return None


@dataclass(frozen=True)
class WikiSummary:
    """Simple container for wiki summary content."""

    title: str
    extract: str
    url: str | None
    language: str
    raw: dict[str, Any] | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.extract)

    @property
    def first_paragraph(self) -> str:
        paragraph, is_html = self._first_paragraph_body()
        if not paragraph:
            return ""

        if is_html:
            return bleach.clean(paragraph, tags=[], strip=True)

        return paragraph

    @property
    def first_paragraph_html(self) -> str:
        paragraph, is_html = self._first_paragraph_body()
        if not paragraph:
            return ""

        allowed_tags = ["a", "b", "strong", "i", "em", "u", "span", "sup", "sub", "code", "br"]
        allowed_attributes = {"a": ["href", "title", "rel"]}
        cleaned = bleach.clean(
            paragraph,
            tags=allowed_tags,
            attributes=allowed_attributes,
            protocols=["http", "https"],
            strip=True,
        )
        linked = bleach.linkify(cleaned)
        return mark_safe(linked)

    def _first_paragraph_body(self) -> tuple[str, bool]:
        text = (self.extract or "").strip()
        if not text:
            return "", False

        html_match = re.search(r"<p[^>]*>(.*?)</p>", text, flags=re.IGNORECASE | re.DOTALL)
        if html_match:
            return html_match.group(1).strip(), True

        for paragraph in re.split(r"\n\s*\n", text):
            cleaned = paragraph.strip()
            if cleaned:
                return cleaned, False

        return text, False


__all__ = [
    "WikimediaBridge",
    "WikiSummary",
    "DEFAULT_WIKIMEDIA_ENDPOINT",
    "DEFAULT_USER_AGENT",
]
