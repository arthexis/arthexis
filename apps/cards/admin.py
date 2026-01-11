from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.cards.forms import CardFaceAdminForm, CardFacePreviewForm
from apps.cards.models import CardFace, RFID
from apps.core.admin import RFIDAdmin


@admin.register(CardFace)
class CardFaceAdmin(admin.ModelAdmin):
    form = CardFaceAdminForm
    list_display = ("name", "fixed_back", "preview_action")
    readonly_fields = ("preview_action", "background_metadata")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "background_media",
                    "background_upload",
                    "background_metadata",
                    "fixed_back",
                    "preview_action",
                )
            },
        ),
        (
            _("Overlay 1"),
            {
                "fields": (
                    "overlay_one_text",
                    "overlay_one_font",
                    "overlay_one_font_size",
                    "overlay_one_x",
                    "overlay_one_y",
                )
            },
        ),
        (
            _("Overlay 2"),
            {
                "fields": (
                    "overlay_two_text",
                    "overlay_two_font",
                    "overlay_two_font_size",
                    "overlay_two_x",
                    "overlay_two_y",
                )
            },
        ),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name="cards_cardface_preview",
            ),
        ]
        return custom + urls

    def preview_action(self, obj: CardFace):  # pragma: no cover - display helper
        if not obj.pk:
            return ""
        url = reverse("admin:cards_cardface_preview", args=[obj.pk])
        return format_html('<a class="button" href="{}">{}</a>', url, _("Preview"))

    preview_action.short_description = _("Preview")

    def preview_view(self, request, object_id):
        card_face = self.get_object(request, object_id)
        if card_face is None:
            return self._get_obj_does_not_exist_redirect(request, CardFace._meta, object_id)

        initial = {
            "overlay_one_text": request.GET.get("overlay_one_text", card_face.overlay_one_text),
            "overlay_two_text": request.GET.get("overlay_two_text", card_face.overlay_two_text),
            "overlay_one_font": request.GET.get("overlay_one_font", card_face.overlay_one_font),
            "overlay_two_font": request.GET.get("overlay_two_font", card_face.overlay_two_font),
            "overlay_one_font_size": request.GET.get("overlay_one_font_size", card_face.overlay_one_font_size),
            "overlay_two_font_size": request.GET.get("overlay_two_font_size", card_face.overlay_two_font_size),
            "overlay_one_x": request.GET.get("overlay_one_x", card_face.overlay_one_x),
            "overlay_one_y": request.GET.get("overlay_one_y", card_face.overlay_one_y),
            "overlay_two_x": request.GET.get("overlay_two_x", card_face.overlay_two_x),
            "overlay_two_y": request.GET.get("overlay_two_y", card_face.overlay_two_y),
        }

        sigil_tokens = CardFace.collect_sigils(initial["overlay_one_text"], initial["overlay_two_text"])
        form = CardFacePreviewForm(
            request.GET or None,
            fonts=CardFace.font_choices(),
            sigils=sigil_tokens,
            initial=initial,
        )
        for name in (
            "overlay_one_text",
            "overlay_two_text",
            "overlay_one_font",
            "overlay_two_font",
            "overlay_one_font_size",
            "overlay_two_font_size",
            "overlay_one_x",
            "overlay_one_y",
            "overlay_two_x",
            "overlay_two_y",
        ):
            if name in form.fields:
                form.fields[name].widget.attrs.setdefault("data-autosubmit", "true")
        cleaned = initial.copy()
        if form.is_bound and form.is_valid():
            cleaned.update(form.cleaned_data)
        overrides = form.sigil_overrides()

        sigil_fields = []
        for token in sigil_tokens:
            field_name = CardFace.sigil_field_name(token)
            if field_name in form.fields:
                sigil_fields.append({
                    "token": token,
                    "field_name": field_name,
                    "field": form[field_name],
                })

        resolved_one = CardFace.resolve_text(cleaned.get("overlay_one_text", ""), current=card_face, overrides=overrides)
        resolved_two = CardFace.resolve_text(cleaned.get("overlay_two_text", ""), current=card_face, overrides=overrides)

        preview = card_face.render_preview(
            overlay_one_text=resolved_one,
            overlay_two_text=resolved_two,
            overlay_one_font=cleaned.get("overlay_one_font") or card_face.overlay_one_font,
            overlay_two_font=cleaned.get("overlay_two_font") or card_face.overlay_two_font,
            overlay_one_size=int(cleaned.get("overlay_one_font_size") or card_face.overlay_one_font_size),
            overlay_two_size=int(cleaned.get("overlay_two_font_size") or card_face.overlay_two_font_size),
            overlay_one_position=(int(cleaned.get("overlay_one_x") or 0), int(cleaned.get("overlay_one_y") or 0)),
            overlay_two_position=(int(cleaned.get("overlay_two_x") or 0), int(cleaned.get("overlay_two_y") or 0)),
        )

        context = {
            **self.admin_site.each_context(request),
            "title": _("Preview Card Face"),
            "opts": self.model._meta,
            "card_face": card_face,
            "form": form,
            "sigil_fields": sigil_fields,
            "preview_image": preview,
        }
        return TemplateResponse(request, "cards/admin/cardface_preview.html", context)

    @admin.display(description=_("Background metadata"))
    def background_metadata(self, obj: CardFace) -> str:
        media = getattr(obj, "background_media", None)
        if not media:
            return _("No background uploaded")
        return _("%(name)s (%(type)s, %(size)s bytes)") % {
            "name": media.original_name or media.file.name,
            "type": media.content_type or _("unknown"),
            "size": media.size,
        }


admin.site.register(RFID, RFIDAdmin)
