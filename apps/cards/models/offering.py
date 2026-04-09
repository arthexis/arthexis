from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, models

from apps.base.models import Entity
from apps.cards.soul import PACKAGE_MAX_BYTES, derive_soul_package


class OfferingSoul(Entity):
    """Deterministic, compact Soul package derived from an uploaded offering file."""

    schema_version = models.CharField(max_length=16, default="1.0")
    hash_algorithm = models.CharField(max_length=16, default="sha256")
    core_hash = models.CharField(max_length=64, unique=True)
    issuance_marker = models.CharField(max_length=64, blank=True, default="")

    filename = models.CharField(max_length=255, blank=True, default="")
    extension = models.CharField(max_length=32, blank=True, default="")
    mime_type = models.CharField(max_length=255, blank=True, default="application/octet-stream")
    file_size_bytes = models.PositiveIntegerField(default=0)

    package = models.JSONField(default=dict, blank=True)
    structural_traits = models.JSONField(default=dict, blank=True)
    type_traits = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-id",)
        verbose_name = "Offering Soul"
        verbose_name_plural = "Offering Souls"

    def clean(self):
        super().clean()
        payload = self.package or {}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > PACKAGE_MAX_BYTES:
            raise ValidationError({"package": "Soul package exceeds 512 KB limit."})

    @classmethod
    def create_from_upload(
        cls,
        uploaded_file: UploadedFile,
        *,
        issuance_marker: str = "",
    ) -> "OfferingSoul":
        package = derive_soul_package(uploaded_file, issuance_marker=issuance_marker)
        metadata = package.get("metadata", {})
        traits = package.get("traits", {})
        core_hash = str(package["core_hash"])
        defaults = {
            "schema_version": str(package.get("schema_version", "1.0")),
            "hash_algorithm": str(package.get("hash_algorithm", "sha256")),
            "issuance_marker": str(package.get("issuance_marker", "")),
            "filename": str(metadata.get("filename", "")),
            "extension": str(metadata.get("extension", "")),
            "mime_type": str(metadata.get("mime_type", "application/octet-stream")),
            "file_size_bytes": int(metadata.get("size_bytes", 0)),
            "package": package,
            "structural_traits": traits.get("structural", {}),
            "type_traits": traits.get("type_aware", {}),
        }
        try:
            soul, _ = cls.objects.get_or_create(core_hash=core_hash, defaults=defaults)
        except IntegrityError:
            soul = cls.objects.get(core_hash=core_hash)
        return soul
