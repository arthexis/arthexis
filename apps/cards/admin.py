from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.cards.forms import (
    CardFaceAdminForm,
    CardFacePreviewForm,
    CardSetUploadForm,
)
from apps.cards.models import (
    RFID,
    CardDesign,
    CardFace,
    CardSet,
    RFIDAttempt,
    RFIDCommandExecution,
    RFIDCommandTemplate,
    RFIDWatchlistEntry,
    RFIDWatchlistEvent,
)
from apps.core.admin import RFIDAdmin
from apps.locals.user_data import EntityModelAdmin


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
            return self._get_obj_does_not_exist_redirect(
                request, CardFace._meta, object_id
            )

        initial = {
            "overlay_one_text": request.GET.get(
                "overlay_one_text", card_face.overlay_one_text
            ),
            "overlay_two_text": request.GET.get(
                "overlay_two_text", card_face.overlay_two_text
            ),
            "overlay_one_font": request.GET.get(
                "overlay_one_font", card_face.overlay_one_font
            ),
            "overlay_two_font": request.GET.get(
                "overlay_two_font", card_face.overlay_two_font
            ),
            "overlay_one_font_size": request.GET.get(
                "overlay_one_font_size", card_face.overlay_one_font_size
            ),
            "overlay_two_font_size": request.GET.get(
                "overlay_two_font_size", card_face.overlay_two_font_size
            ),
            "overlay_one_x": request.GET.get("overlay_one_x", card_face.overlay_one_x),
            "overlay_one_y": request.GET.get("overlay_one_y", card_face.overlay_one_y),
            "overlay_two_x": request.GET.get("overlay_two_x", card_face.overlay_two_x),
            "overlay_two_y": request.GET.get("overlay_two_y", card_face.overlay_two_y),
        }

        sigil_tokens = CardFace.collect_sigils(
            initial["overlay_one_text"], initial["overlay_two_text"]
        )
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
                sigil_fields.append(
                    {
                        "token": token,
                        "field_name": field_name,
                        "field": form[field_name],
                    }
                )

        resolved_one = CardFace.resolve_text(
            cleaned.get("overlay_one_text", ""), current=card_face, overrides=overrides
        )
        resolved_two = CardFace.resolve_text(
            cleaned.get("overlay_two_text", ""), current=card_face, overrides=overrides
        )

        preview = card_face.render_preview(
            overlay_one_text=resolved_one,
            overlay_two_text=resolved_two,
            overlay_one_font=cleaned.get("overlay_one_font")
            or card_face.overlay_one_font,
            overlay_two_font=cleaned.get("overlay_two_font")
            or card_face.overlay_two_font,
            overlay_one_size=int(
                cleaned.get("overlay_one_font_size") or card_face.overlay_one_font_size
            ),
            overlay_two_size=int(
                cleaned.get("overlay_two_font_size") or card_face.overlay_two_font_size
            ),
            overlay_one_position=(
                int(cleaned.get("overlay_one_x") or 0),
                int(cleaned.get("overlay_one_y") or 0),
            ),
            overlay_two_position=(
                int(cleaned.get("overlay_two_x") or 0),
                int(cleaned.get("overlay_two_y") or 0),
            ),
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


@admin.register(RFIDCommandTemplate)
class RFIDCommandTemplateAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "title",
        "command_name",
        "lifecycle_mode",
        "source",
        "view_kind",
        "is_active",
        "public_url_link",
    )
    list_filter = ("source", "view_kind", "lifecycle_mode", "is_active")
    search_fields = ("name", "title", "description", "command_name")
    readonly_fields = (
        "payload_digest_display",
        "public_url_link",
        "qr_preview",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "title",
                    "description",
                    "instructions",
                    "source",
                    "is_active",
                    "requires_owner",
                )
            },
        ),
        (
            _("Command"),
            {
                "fields": (
                    "command_name",
                    "command_params",
                    "command_sigils",
                    "lifecycle_mode",
                    "payload_digest_display",
                )
            },
        ),
        (
            _("Public view"),
            {
                "fields": (
                    "view_kind",
                    "qr_target_path",
                    "public_url_link",
                    "qr_preview",
                )
            },
        ),
    )

    @admin.display(description=_("Payload digest"))
    def payload_digest_display(self, obj: RFIDCommandTemplate) -> str:
        if not obj.pk:
            return ""
        return obj.payload_digest

    @admin.display(description=_("Public URL"))
    def public_url_link(self, obj: RFIDCommandTemplate) -> str:
        if not obj.pk:
            return ""
        url = obj.get_absolute_url()
        return format_html('<a href="{}">{}</a>', url, url)

    @admin.display(description=_("QR preview"))
    def qr_preview(self, obj: RFIDCommandTemplate) -> str:
        if not obj.pk:
            return ""
        data_uri = obj.qr_data_uri(obj.get_qr_target_path())
        if not data_uri:
            return _("QR generation is unavailable")
        return format_html(
            '<img src="{}" alt="{}" style="max-width: 12rem; background: #fff; padding: .5rem;">',
            data_uri,
            _("QR code"),
        )


@admin.register(RFIDAttempt)
class RFIDAttemptAdmin(EntityModelAdmin):
    list_display = (
        "label",
        "rfid",
        "status",
        "source",
        "charger",
        "account",
        "transaction",
        "attempted_at",
    )
    list_filter = ("status", "source")
    search_fields = (
        "rfid",
        "label__label_id",
        "label__rfid",
        "charger__charger_id",
        "account__name",
        "transaction__ocpp_id",
    )
    readonly_fields = ("attempted_at",)


@admin.register(RFIDWatchlistEntry)
class RFIDWatchlistEntryAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "label",
        "normalized_rfid",
        "enabled",
        "action_type",
        "rate_limit_seconds",
        "last_matched_at",
    )
    list_filter = ("enabled", "action_type")
    search_fields = ("name", "normalized_rfid", "label__rfid", "label__custom_label")
    raw_id_fields = ("label",)
    readonly_fields = ("last_matched_at", "created_at", "updated_at")


@admin.register(RFIDWatchlistEvent)
class RFIDWatchlistEventAdmin(EntityModelAdmin):
    actions = None
    list_display = (
        "entry",
        "rfid",
        "source",
        "status",
        "retry_count",
        "created_at",
        "processed_at",
    )
    list_filter = ("status", "source")
    search_fields = ("rfid", "entry__name", "idempotency_key")
    raw_id_fields = ("entry", "attempt", "label")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return super().has_change_permission(request, obj)
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(RFIDCommandExecution)
class RFIDCommandExecutionAdmin(EntityModelAdmin):
    actions = None
    list_display = (
        "card_name",
        "rfid_value",
        "command_name",
        "status",
        "run_as_user",
        "reader_id",
        "triggered_at",
        "completed_at",
    )
    list_select_related = ("run_as_user",)
    list_filter = ("status", "command_name", "reader_id")
    search_fields = (
        "execution_id",
        "rfid_value",
        "card_name",
        "command_name",
        "status_detail",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return super().has_change_permission(request, obj)
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


class CardDesignInline(admin.TabularInline):
    model = CardDesign
    extra = 0
    can_delete = False
    show_change_link = True
    readonly_fields = ("sequence", "name")
    fields = ("sequence", "name")


@admin.register(CardSet)
class CardSetAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "game", "style", "created_on")
    search_fields = ("name", "code", "game", "style")
    readonly_fields = ("created_on",)
    inlines = (CardDesignInline,)
    change_list_template = "admin/cards/cardset/change_list.html"

    def get_urls(self):
        custom = [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_view),
                name="cards_cardset_upload",
            ),
        ]
        return custom + super().get_urls()

    def upload_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_add_permission(request):
            messages.error(
                request, _("You do not have permission to upload card sets.")
            )
            return redirect("admin:index")

        form = CardSetUploadForm(request.POST or None, request.FILES or None)

        if request.method == "POST" and form.is_valid():
            card_set = form.save()
            messages.success(
                request,
                _("Imported card set '%(name)s' with %(count)d cards.")
                % {"name": card_set.name, "count": card_set.card_designs.count()},
            )
            return redirect(reverse("admin:cards_cardset_change", args=[card_set.pk]))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "title": _("Upload card set"),
        }
        return TemplateResponse(request, "admin/cards/cardset/upload.html", context)


@admin.register(CardDesign)
class CardDesignAdmin(admin.ModelAdmin):
    list_display = ("name", "card_set", "sequence", "created_on")
    list_filter = ("card_set",)
    search_fields = ("name", "card_set__name")
    readonly_fields = ("created_on",)
