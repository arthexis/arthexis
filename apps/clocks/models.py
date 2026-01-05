from __future__ import annotations
from typing import Iterable

from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class ClockDevice(Entity):
    """Detected clock device available to a node."""

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="clock_devices"
    )
    bus = models.PositiveIntegerField(default=1)
    address = models.CharField(max_length=10)
    description = models.CharField(max_length=255, blank=True)
    raw_info = models.TextField(blank=True)
    enable_public_view = models.BooleanField(default=False)
    public_view_slug = models.SlugField(unique=True, blank=True, null=True)

    class Meta:
        ordering = ["bus", "address"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "bus", "address"], name="clocks_clockdevice_unique"
            )
        ]
        verbose_name = _("Clock Device")
        verbose_name_plural = _("Clock Devices")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.address} (bus {self.bus})"

    def save(self, *args, **kwargs):
        generate_slug = not self.public_view_slug
        super().save(*args, **kwargs)
        if generate_slug and self.pk and not self.public_view_slug:
            slug = slugify(f"clock-device-{self.pk}")
            type(self).objects.filter(pk=self.pk).update(public_view_slug=slug)
            self.public_view_slug = slug

    @classmethod
    def refresh_from_system(
        cls,
        *,
        node,
        bus_numbers: Iterable[int] | None = None,
        scanner=None,
    ) -> tuple[int, int]:
        """Synchronize :class:`ClockDevice` entries for ``node``.

        Returns a ``(created, updated)`` tuple.
        """

        from .utils import discover_clock_devices

        detected = discover_clock_devices(
            bus_numbers=bus_numbers or (1,), scanner=scanner
        )
        created = 0
        updated = 0
        existing = {
            (device.bus, device.address): device
            for device in cls.objects.filter(node=node)
        }
        seen: set[tuple[int, str]] = set()
        for device in detected:
            key = (device.bus, device.address)
            seen.add(key)
            obj = existing.get(key)
            defaults = {
                "description": device.description,
                "raw_info": device.raw_info,
            }
            if obj is None:
                cls.objects.create(
                    node=node,
                    bus=device.bus,
                    address=device.address,
                    **defaults,
                )
                created += 1
            else:
                dirty = False
                for field, value in defaults.items():
                    if getattr(obj, field) != value:
                        setattr(obj, field, value)
                        dirty = True
                if dirty:
                    obj.save(update_fields=list(defaults.keys()))
                    updated += 1
        for obj in cls.objects.filter(node=node):
            if (obj.bus, obj.address) not in seen:
                obj.delete()
        return created, updated

    @classmethod
    def has_clock_device(cls, *, bus_numbers: Iterable[int] | None = None, scanner=None) -> bool:
        """Return ``True`` when a clock device is available."""

        from .utils import discover_clock_devices

        return bool(discover_clock_devices(bus_numbers=bus_numbers, scanner=scanner))
