import hashlib
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import models
import qrcode


class QRLink(models.Model):
    """Store a value and the generated QR code image for it."""

    value = models.CharField(max_length=2000, unique=True)
    image = models.ImageField(upload_to="qr_codes/", blank=True)

    def save(self, *args, **kwargs):
        if not self.image:
            qr = qrcode.QRCode(box_size=10, border=4)
            qr.add_data(self.value)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            filename = hashlib.sha256(self.value.encode()).hexdigest()[:16] + ".png"
            self.image.save(filename, ContentFile(buffer.getvalue()), save=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.value
