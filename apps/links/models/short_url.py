"""Short URL model and helper APIs."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity

from .validators import _is_valid_redirect_target


class ShortURL(Entity):
    """Short URL slug that can redirect to an updated target over time."""

    slug = models.SlugField(
        max_length=32,
        unique=True,
        blank=True,
        help_text=_("Short, persistent slug used for shared links."),
    )
    original_url = models.CharField(
        max_length=500,
        db_index=True,
        help_text=_("Original URL used when the short link was created."),
    )
    target_url = models.CharField(
        max_length=500,
        db_index=True,
        help_text=_("Destination URL or absolute path."),
    )
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Short URL")
        verbose_name_plural = _("Short URLs")
        ordering = ("slug",)

    def __str__(self) -> str:
        return self.slug

    def clean(self):
        """Validate the redirect destination."""

        super().clean()
        if self.target_url and not _is_valid_redirect_target(self.target_url.strip()):
            raise ValidationError(
                {"target_url": _("Enter a valid http(s) URL or absolute path.")}
            )

    def save(self, *args, **kwargs):
        """Persist the short URL and lazily generate a unique slug."""

        self.target_url = (self.target_url or "").strip()
        self.original_url = (self.original_url or "").strip()
        if self.slug or self.pk is not None:
            super().save(*args, **kwargs)
            return
        for _ in range(5):
            from apps.links import models as links_models

            self.slug = links_models._generate_short_slug()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                    return
            except IntegrityError:
                self.pk = None
        raise IntegrityError("Failed to generate a unique slug after multiple attempts.")

    def redirect_path(self) -> str:
        return reverse("links:short-url", args=[self.slug])


def get_or_create_short_url(target_url: str) -> ShortURL | None:
    """Get or create a :class:`ShortURL` for ``target_url``."""

    target_url = (target_url or "").strip()
    if not target_url:
        return None
    existing = ShortURL.objects.filter(target_url=target_url).order_by("pk").first()
    if existing:
        return existing
    existing = ShortURL.objects.filter(original_url=target_url).order_by("pk").first()
    if existing:
        return existing
    return ShortURL.objects.create(original_url=target_url, target_url=target_url)
