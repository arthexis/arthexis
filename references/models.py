import hashlib
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import models
import qrcode


class Reference(models.Model):
    """Store a reference value and optional generated QR image."""

    value = models.CharField(max_length=2000, unique=True)
    alt_text = models.CharField(max_length=500, blank=True)
    image = models.ImageField(upload_to="references/", blank=True)
    uses = models.PositiveIntegerField(default=0)
    method = models.CharField(max_length=50, default="qr")
    include_in_footer = models.BooleanField(default=False, verbose_name="Include in Footer")
    is_seed_data = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.method == "qr":
            qr = qrcode.QRCode(box_size=10, border=4)
            qr.add_data(self.value)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            filename = hashlib.sha256(self.value.encode()).hexdigest()[:16] + ".png"
            if self.image:
                self.image.delete(save=False)
            self.image.save(filename, ContentFile(buffer.getvalue()), save=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.value

