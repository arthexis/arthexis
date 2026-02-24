"""QR redirect-related models."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.leads.models import Lead

from .validators import _is_valid_redirect_target


class QRRedirect(Entity):
    """Short-slug QR redirect that can be updated without changing the slug."""

    slug = models.SlugField(
        max_length=32,
        unique=True,
        blank=True,
        help_text=_("Short, persistent slug used for QR links."),
    )
    target_url = models.CharField(
        max_length=500,
        help_text=_("Destination URL or absolute path."),
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Optional display title for the public view."),
    )
    text_above = models.TextField(
        blank=True,
        help_text=_("Optional text to show above the embedded content."),
    )
    text_below = models.TextField(
        blank=True,
        help_text=_("Optional text to show below the embedded content."),
    )
    is_public = models.BooleanField(
        default=False,
        help_text=_("Controls visibility in the public QR listing."),
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("QR Redirect")
        verbose_name_plural = _("QR Redirects")
        ordering = ("slug",)

    def __str__(self) -> str:
        return self.title or self.slug

    def clean(self):
        """Validate the redirect destination."""

        super().clean()
        if self.target_url and not _is_valid_redirect_target(self.target_url.strip()):
            raise ValidationError(
                {"target_url": _("Enter a valid http(s) URL or absolute path.")}
            )

    def save(self, *args, **kwargs):
        """Persist the redirect and lazily generate a unique slug."""

        self.target_url = (self.target_url or "").strip()
        if self.slug or self.pk is not None:
            super().save(*args, **kwargs)
            return
        for _ in range(5):
            from apps.links import models as links_models

            self.slug = links_models._generate_qr_slug()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                    return
            except IntegrityError:
                self.pk = None
        raise IntegrityError("Failed to generate a unique slug after multiple attempts.")

    def redirect_path(self) -> str:
        return reverse("links:qr-redirect", args=[self.slug])

    def public_path(self) -> str:
        return reverse("links:qr-redirect-public", args=[self.slug])


class QRRedirectLead(Lead):
    """Lead capture rows associated with public QR redirect visits."""

    qr_redirect = models.ForeignKey(
        QRRedirect,
        on_delete=models.CASCADE,
        related_name="leads",
    )
    target_url = models.TextField(blank=True)

    class Meta:
        verbose_name = _("QR Redirect Lead")
        verbose_name_plural = _("QR Redirect Leads")
