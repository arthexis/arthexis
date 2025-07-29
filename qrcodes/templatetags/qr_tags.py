from django import template
from django.utils.safestring import mark_safe

from qrcodes.models import QRLink

register = template.Library()


@register.simple_tag
def qr_img(value, size=200):
    """Return an <img> tag with the QR code for the given value."""
    qr, _ = QRLink.objects.get_or_create(value=value)
    return mark_safe(
        f'<img src="{qr.image.url}" width="{size}" height="{size}" alt="QR code" />'
    )

