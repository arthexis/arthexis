from __future__ import annotations

from io import BytesIO

from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.locals.user_data import EntityModelAdmin

from ..models import PublicConnectorPage


@admin.register(PublicConnectorPage)
class PublicConnectorPageAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "enabled",
        "slug",
        "updated_at",
    )
    list_filter = ("enabled",)
    search_fields = (
        "charger__charger_id",
        "charger__display_name",
        "title",
    )
    autocomplete_fields = ("charger",)
    raw_id_fields = ("language",)
    readonly_fields = ("slug", "qr_svg", "created_at", "updated_at")
    actions = ["regenerate_qr_assets", "download_qr_assets", "download_sticker_sheet"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "charger",
                    "enabled",
                    "title",
                    "language",
                )
            },
        ),
        (
            _("Instructions"),
            {
                "fields": (
                    "instructions_markdown",
                    "rules_markdown",
                )
            },
        ),
        (
            _("Support"),
            {
                "fields": (
                    "support_phone",
                    "support_whatsapp",
                    "support_email",
                    "support_url",
                )
            },
        ),
        (
            _("QR Assets"),
            {"fields": ("qr_svg", "qr_png")},
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at")},
        ),
    )

    def regenerate_qr_assets(self, request, queryset):
        if not queryset:
            self.message_user(
                request,
                _("Select at least one public page to regenerate."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(request.get_full_path())

        updated = 0
        for page in queryset:
            page.refresh_qr_assets(page.public_url(request))
            page.save(update_fields=["qr_svg", "qr_png"])
            updated += 1

        self.message_user(
            request,
            ngettext(
                "Regenerated QR assets for %(count)d page.",
                "Regenerated QR assets for %(count)d pages.",
                updated,
            )
            % {"count": updated},
            level=messages.SUCCESS,
        )

    regenerate_qr_assets.short_description = _(  # type: ignore[attr-defined]
        "Regenerate QR assets"
    )

    def download_qr_assets(self, request, queryset):
        pages = list(queryset)
        if not pages:
            self.message_user(
                request,
                _("Select at least one public page to download."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(request.get_full_path())

        if len(pages) > 1:
            self.message_user(
                request,
                _("Please select a single public page to download QR assets."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(request.get_full_path())

        page = pages[0]
        if not page.qr_png:
            page.refresh_qr_assets(page.public_url(request))
            page.save(update_fields=["qr_svg", "qr_png"])

        if not page.qr_png:
            self.message_user(
                request,
                _("QR assets were not available for this page."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(request.get_full_path())

        response = HttpResponse(page.qr_png.read(), content_type="image/png")
        filename = f"{slugify(page.display_title()) or page.slug}.png"
        response["Content-Disposition"] = f"attachment; filename={filename}"
        return response

    download_qr_assets.short_description = _(  # type: ignore[attr-defined]
        "Download QR PNG"
    )

    def download_sticker_sheet(self, request, queryset):
        pages = list(queryset)
        if not pages:
            self.message_user(
                request,
                _("Select at least one public page to print."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(request.get_full_path())

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        margin = 12 * mm
        label_width = (width - margin * 2) / 2
        label_height = (height - margin * 2) / 4
        columns = 2
        rows = 4
        qr_target = 28 * mm
        font_label = "Helvetica-Bold"
        font_text = "Helvetica"

        page_index = 0
        for index, page in enumerate(pages):
            if index % (columns * rows) == 0:
                if page_index:
                    pdf.showPage()
                page_index += 1
                pdf.setFillColor(colors.white)
                pdf.rect(0, 0, width, height, stroke=0, fill=1)

            column = index % columns
            row = (index // columns) % rows
            x = margin + column * label_width
            y = height - margin - (row + 1) * label_height

            pdf.setStrokeColor(colors.lightgrey)
            pdf.rect(x + 1, y + 1, label_width - 2, label_height - 2, stroke=1, fill=0)

            label_title = page.display_title()
            connector_label = page.charger.connector_label
            subtitle = f"{label_title} — {connector_label}"

            pdf.setFont(font_label, 9)
            pdf.setFillColor(colors.black)
            pdf.drawString(x + 8 * mm, y + label_height - 8 * mm, subtitle[:60])

            pdf.setFont(font_text, 8)
            pdf.drawString(
                x + 8 * mm,
                y + label_height - 14 * mm,
                _("Scan for instructions"),
            )

            qr_url = page.public_url(request)
            drawing = Drawing(qr_target, qr_target)
            qr_code = qr.QrCodeWidget(qr_url)
            bounds = qr_code.getBounds()
            size = bounds[2] - bounds[0]
            scale = qr_target / size
            qr_code.scale(scale, scale)
            drawing.add(qr_code)
            renderPDF.draw(drawing, pdf, x + 8 * mm, y + 8 * mm)

        pdf.save()
        buffer.seek(0)

        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = "attachment; filename=connector-stickers.pdf"
        return response

    download_sticker_sheet.short_description = _(  # type: ignore[attr-defined]
        "Download sticker sheet"
    )
