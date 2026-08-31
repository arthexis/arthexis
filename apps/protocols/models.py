from django.db import models


class ProtocolManager(models.Manager):
    def get_by_natural_key(self, slug: str):  # pragma: no cover - used by fixtures
        return self.get(slug=slug)


class ProtocolCallManager(models.Manager):
    def get_by_natural_key(  # pragma: no cover - used by fixtures
        self, protocol_slug: str, name: str, direction: str
    ):
        return self.select_related("protocol").get(
            protocol__slug=protocol_slug, name=name, direction=direction
        )


class Protocol(models.Model):
    """High-level protocol descriptor (e.g., OCPP 1.6)."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    version = models.CharField(max_length=32)
    description = models.TextField(blank=True)

    objects = ProtocolManager()

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:  # pragma: no cover - trivial repr
        return f"{self.name} {self.version}".strip()

    def natural_key(self):  # pragma: no cover - used by fixtures
        return (self.slug,)


class ProtocolCall(models.Model):
    """Individual call supported by a protocol."""

    CP_TO_CSMS = "cp_to_csms"
    CSMS_TO_CP = "csms_to_cp"
    DIRECTIONS = (
        (CP_TO_CSMS, "Charge point to CSMS"),
        (CSMS_TO_CP, "CSMS to charge point"),
    )

    protocol = models.ForeignKey(Protocol, on_delete=models.CASCADE, related_name="calls")
    name = models.CharField(max_length=128)
    direction = models.CharField(max_length=16, choices=DIRECTIONS)

    objects = ProtocolCallManager()

    class Meta:
        ordering = ["protocol__slug", "name"]
        unique_together = [("protocol", "name", "direction")]

    def __str__(self) -> str:  # pragma: no cover - trivial repr
        return f"{self.protocol.slug}:{self.name} ({self.direction})"

    def natural_key(self):  # pragma: no cover - used by fixtures
        return (*self.protocol.natural_key(), self.name, self.direction)

