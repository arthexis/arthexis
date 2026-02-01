from __future__ import annotations

import uuid
from io import BytesIO

import qrcode
from qrcode.image.svg import SvgImage
from django.core.files.base import ContentFile
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.locale.models import Language

from .charger import Charger


class PublicConnectorPage(models.Model):
    charger = models.OneToOneField(
        Charger,
        on_delete=models.CASCADE,
        related_name="public_page",
        verbose_name=_("Charge Point"),
    )
    slug = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    enabled = models.BooleanField(default=True)
    title = models.CharField(max_length=200, blank=True)
    instructions_markdown = models.TextField(blank=True)
    rules_markdown = models.TextField(blank=True)
    support_phone = models.CharField(max_length=50, blank=True)
    support_whatsapp = models.CharField(max_length=50, blank=True)
    support_email = models.EmailField(blank=True)
    support_url = models.URLField(blank=True)
    language = models.ForeignKey(
        Language,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="public_connector_pages",
    )
    qr_svg = models.TextField(blank=True)
    qr_png = models.ImageField(upload_to="ocpp/public_pages/qr/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Public Connector Page")
        verbose_name_plural = _("Public Connector Pages")

    def __str__(self) -> str:  # pragma: no cover - simple display
        return f"{self.charger} public page"

    def public_path(self) -> str:
        return reverse("ocpp:public-connector-page", args=[self.slug])

    def public_url(self, request=None) -> str:
        path = self.public_path()
        if request is not None:
            return request.build_absolute_uri(path)
        return path

    def display_title(self) -> str:
        return (
            self.title
            or self.charger.display_name
            or self.charger.name
            or self.charger.charger_id
        )

    def language_code(self) -> str:
        language = getattr(self, "language", None)
        return (language.code or "").strip() if language else ""

    def generate_qr_assets(self, url: str) -> tuple[str, bytes]:
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)

        svg_image = qr.make_image(image_factory=SvgImage)
        svg_buffer = BytesIO()
        svg_image.save(svg_buffer)
        svg_text = svg_buffer.getvalue().decode("utf-8")

        png_image = qr.make_image(fill_color="black", back_color="white")
        png_buffer = BytesIO()
        png_image.save(png_buffer, format="PNG")
        return svg_text, png_buffer.getvalue()

    def refresh_qr_assets(self, url: str) -> None:
        svg_text, png_bytes = self.generate_qr_assets(url)
        self.qr_svg = svg_text
        filename = f"{self.slug}.png"
        self.qr_png.save(filename, ContentFile(png_bytes), save=False)


class PublicScanEvent(models.Model):
    page = models.ForeignKey(
        PublicConnectorPage,
        on_delete=models.CASCADE,
        related_name="scan_events",
        verbose_name=_("Public connector page"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    user_agent = models.CharField(max_length=255, blank=True)
    referrer = models.URLField(blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = _("Public Scan Event")
        verbose_name_plural = _("Public Scan Events")

    def __str__(self) -> str:  # pragma: no cover - simple display
        return f"{self.page_id} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
