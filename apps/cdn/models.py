"""Models for configuring CDN providers used for static assets."""

from __future__ import annotations

from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class CDNConfiguration(Entity):
    """Represents a CDN endpoint that can serve static assets."""

    class Provider(models.TextChoices):
        AWS_CLOUDFRONT = "aws_cloudfront", "AWS CloudFront"
        CLOUDFLARE = "cloudflare", "Cloudflare CDN"
        JSDELIVR = "jsdelivr", "jsDelivr"

    name = models.CharField(
        max_length=120,
        unique=True,
        help_text=_("Human-readable label for this CDN configuration."),
    )
    provider = models.CharField(
        max_length=32,
        choices=Provider.choices,
        default=Provider.CLOUDFLARE,
        help_text=_("CDN provider backing this endpoint."),
    )
    base_url = models.URLField(
        validators=[URLValidator(schemes=["https"])],
        help_text=_("Public base URL where static assets are served from."),
    )
    aws_distribution_id = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("CloudFront distribution ID (required for AWS CloudFront)."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable this configuration without deleting it."),
    )

    class Meta:
        verbose_name = _("CDN configuration")
        verbose_name_plural = _("CDN configurations")
        db_table = "cdn_configuration"
        constraints = [
            models.CheckConstraint(
                name="cdn_distribution_id_matches_provider",
                condition=(
                    (
                        models.Q(provider="aws_cloudfront")
                        & ~models.Q(aws_distribution_id="")
                    )
                    | (
                        ~models.Q(provider="aws_cloudfront")
                        & models.Q(aws_distribution_id="")
                    )
                ),
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - representation only
        return f"{self.name} ({self.get_provider_display()})"

    def clean(self) -> None:
        """Validate provider-specific requirements for CDN configurations."""

        super().clean()

        errors: dict[str, ValidationError] = {}
        if self.provider == self.Provider.AWS_CLOUDFRONT and not self.aws_distribution_id:
            errors["aws_distribution_id"] = ValidationError(
                _("CloudFront distribution ID is required for AWS CloudFront."),
                code="required",
            )

        if self.provider != self.Provider.AWS_CLOUDFRONT and self.aws_distribution_id:
            errors["aws_distribution_id"] = ValidationError(
                _("Distribution ID can only be set for AWS CloudFront."),
                code="invalid",
            )

        if errors:
            raise ValidationError(errors)
