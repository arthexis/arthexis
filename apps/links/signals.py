from __future__ import annotations

import hashlib
from io import BytesIO

import qrcode
from django.core.files.base import ContentFile
from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.links.models import Reference


@receiver(pre_save, sender=Reference)
def ensure_reference_qr_image(
    sender, instance: Reference, raw: bool = False, **_: object
) -> None:
    """Generate the QR image for references when missing."""

    if raw:
        return
    if instance.image or not instance.value:
        return

    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(instance.value)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    filename = hashlib.sha256(instance.value.encode()).hexdigest()[:16] + ".png"
    instance.image.save(filename, ContentFile(buffer.getvalue()), save=False)
