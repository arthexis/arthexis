"""Base model for payment processors."""

from __future__ import annotations

from typing import Iterable

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class PaymentProcessor(Entity):
    """Abstract base for global payment processors."""

    verified_on = models.DateTimeField(null=True, blank=True)
    verification_reference = models.CharField(max_length=255, blank=True, editable=False)

    verification_fields: Iterable[str] = ()

    class Meta:
        """Model metadata."""

        abstract = True
        verbose_name = _("Payment Processor")
        verbose_name_plural = _("Payment Processors")

    def _clear_verification(self) -> None:
        """Clear stored verification metadata."""

        self.verified_on = None
        self.verification_reference = ""

    def save(self, *args, **kwargs):
        """Reset verification state whenever tracked fields are changed."""

        if self.pk:
            try:
                old = type(self).all_objects.get(pk=self.pk)
            except type(self).DoesNotExist:
                old = None
            if old is not None:
                for field in self.verification_fields:
                    if getattr(old, field, None) != getattr(self, field, None):
                        self._clear_verification()
                        break
        super().save(*args, **kwargs)

    @property
    def is_verified(self) -> bool:
        """Return whether this processor has been verified."""

        return self.verified_on is not None

    def verify(self):  # pragma: no cover - implemented by subclasses
        """Validate stored credentials against the remote payment provider."""

        raise NotImplementedError

    def identifier(self) -> str:
        """Return display identifier for this processor instance."""

        reference = (self.verification_reference or "").strip()
        if reference:
            return reference
        if self.pk:
            return f"{self._meta.verbose_name} #{self.pk}"
        return str(self._meta.verbose_name)

    def __str__(self) -> str:  # pragma: no cover - presentation
        """Return a readable representation."""

        return self.identifier()
