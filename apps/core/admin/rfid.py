from collections import defaultdict
from io import BytesIO
import json

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _, ngettext
from django.views.decorators.csrf import csrf_exempt
from import_export import fields, resources
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.cards.models import RFID
from apps.cards.rfid_import_export import (
    account_column_for_field,
    parse_accounts,
    serialize_accounts,
)
from apps.cards.utils import build_mode_toggle
from apps.energy.models import ClientReport, CustomerAccount
from apps.links.models import ExperienceReference, Reference
from apps.locals.user_data import EntityModelAdmin
from apps.ocpp.models import Transaction
from apps.core.widgets import RFIDDataWidget

from .forms import RFIDConfirmImportForm, RFIDExportForm, RFIDImportForm


class RFIDResource(resources.ModelResource):
    energy_accounts = fields.Field(column_name="energy_accounts", readonly=True)
    reference = fields.Field(
        column_name="reference",
        attribute="reference",
        widget=ForeignKeyWidget(Reference, "value"),
    )

    def __init__(self, *args, account_field: str = "id", **kwargs):
        super().__init__(*args, **kwargs)
        self.account_field = account_field
        account_column = account_column_for_field(account_field)
        self.fields["energy_accounts"].column_name = account_column

    def get_instance(self, instance_loader, row):
        instance = super().get_instance(instance_loader, row)
        if instance is not None:
            return instance

        rfid_field = self.fields.get("rfid")
        if rfid_field is None:
            return None

        raw_value = row.get(rfid_field.column_name)
        normalized = RFID.normalize_code(str(raw_value or ""))
        if not normalized:
            return None

        existing = RFID.find_match(normalized)
        if existing is None:
            return None

        label_field = self.fields.get("label_id")
        if label_field is not None:
            row[label_field.column_name] = str(existing.pk)

        row[rfid_field.column_name] = normalized
        return existing

    def get_queryset(self):
        manager = getattr(self._meta.model, "all_objects", None)
        if manager is not None:
            return manager.all()
        return super().get_queryset()

    def dehydrate_energy_accounts(self, obj):
        return serialize_accounts(obj, self.account_field)

    def after_save_instance(self, instance, row, **kwargs):
        super().after_save_instance(instance, row, **kwargs)
        if kwargs.get("dry_run"):
            return
        accounts = parse_accounts(row, self.account_field)
        if accounts:
            instance.energy_accounts.set(accounts)
        else:
            instance.energy_accounts.clear()

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "is_deleted", False):
            instance.is_deleted = False
        super().before_save_instance(instance, row, **kwargs)

    class Meta:
        model = RFID
        fields = (
            "label_id",
            "rfid",
            "custom_label",
            "energy_accounts",
            "reference",
            "external_command",
            "post_auth_command",
            "allowed",
            "color",
            "endianness",
            "kind",
            "released",
            "expiry_date",
            "last_seen_on",
        )
        export_order = (
            "label_id",
            "rfid",
            "custom_label",
            "energy_accounts",
            "reference",
            "external_command",
            "post_auth_command",
            "allowed",
            "color",
            "endianness",
            "kind",
            "released",
            "expiry_date",
            "last_seen_on",
        )
        import_id_fields = ("label_id",)


class RFIDForm(forms.ModelForm):
    """RFID admin form with optional reference field."""

    class Meta:
        model = RFID
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference"].required = False
        rel = RFID._meta.get_field("reference").remote_field
        rel.model = ExperienceReference
        widget = self.fields["reference"].widget
        self.fields["reference"].widget = RelatedFieldWidgetWrapper(
            widget,
            rel,
            admin.site,
            can_add_related=True,
            can_change_related=True,
            can_view_related=True,
        )
        self.fields["data"].widget = RFIDDataWidget()


class CopyRFIDForm(forms.Form):
    """Simple form to capture the new RFID value when copying a tag."""

    rfid = forms.CharField(
        label=_("New RFID value"),
        max_length=RFID._meta.get_field("rfid").max_length,
        help_text=_("Enter the hexadecimal value for the new card."),
    )

    def clean_rfid(self):
        value = (self.cleaned_data.get("rfid") or "").strip()
        field = RFID._meta.get_field("rfid")
        try:
            cleaned = field.clean(value, None)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)
        normalized = (cleaned or "").strip().upper()
        if not normalized:
            raise forms.ValidationError(_("RFID value is required."))
        if RFID.matching_queryset(normalized).exists():
            raise forms.ValidationError(
                _("An RFID with this value already exists.")
            )
        return normalized


class RFIDAdmin(EntityModelAdmin, ImportExportModelAdmin):
    change_list_template = "admin/cards/rfid/change_list.html"
    resource_class = RFIDResource
    import_form_class = RFIDImportForm
    confirm_form_class = RFIDConfirmImportForm
    export_form_class = RFIDExportForm
    list_display = (
        "label",
        "rfid",
        "color",
        "endianness_short",
        "released",
        "allowed",
        "last_seen_on",
    )
    list_filter = ("color", "endianness", "released", "allowed")
    search_fields = ("label_id", "rfid", "custom_label")
    autocomplete_fields = ["energy_accounts"]
    raw_id_fields = ["reference"]
    actions = [
        "scan_rfids",
        "print_card_labels",
        "print_release_form",
        "copy_rfids",
        "merge_rfids",
        "toggle_selected_released",
        "toggle_selected_allowed",
        "create_account_from_rfid",
    ]
    readonly_fields = ("added_on", "last_seen_on", "reversed_uid", "qr_test_link")
    form = RFIDForm

    def get_import_resource_kwargs(self, request, form=None, **kwargs):
        resource_kwargs = super().get_import_resource_kwargs(
            request, form=form, **kwargs
        )
        account_field = "id"
        if form and hasattr(form, "cleaned_data"):
            account_field = form.cleaned_data.get("account_field") or "id"
        resource_kwargs["account_field"] = (
            "name" if account_field == "name" else "id"
        )
        return resource_kwargs

    def get_confirm_form_initial(self, request, import_form):
        initial = super().get_confirm_form_initial(request, import_form)
        if import_form and hasattr(import_form, "cleaned_data"):
            initial["account_field"] = (
                import_form.cleaned_data.get("account_field") or "id"
            )
        return initial

    def get_export_resource_kwargs(self, request, **kwargs):
        export_form = kwargs.get("export_form")
        resource_kwargs = super().get_export_resource_kwargs(request, **kwargs)
        account_field = "id"
        if export_form and hasattr(export_form, "cleaned_data"):
            account_field = (
                export_form.cleaned_data.get("account_field") or "id"
            )
        resource_kwargs["account_field"] = (
            "name" if account_field == "name" else "id"
        )
        return resource_kwargs

    def label(self, obj):
        return obj.label_id

    label.admin_order_field = "label_id"
    label.short_description = "Label"

    @admin.display(description=_("End"), ordering="endianness")
    def endianness_short(self, obj):
        labels = {
            RFID.BIG_ENDIAN: _("Big"),
            RFID.LITTLE_ENDIAN: _("Little"),
        }
        return labels.get(obj.endianness, obj.get_endianness_display())

    def scan_rfids(self, request, queryset):
        return redirect("admin:cards_rfid_scan")

    scan_rfids.short_description = "Scan RFIDs"

    @staticmethod
    def _build_unique_account_name(base: str) -> str:
        base_name = (base or "").strip().upper() or "RFID ACCOUNT"
        candidate = base_name
        suffix = 1
        while CustomerAccount.objects.filter(name=candidate).exists():
            suffix += 1
            candidate = f"{base_name}-{suffix}"
        return candidate

    @admin.action(description=_("Create Account from RFID"))
    def create_account_from_rfid(self, request, queryset):
        created = 0
        reassigned = 0
        skipped = 0

        for tag in queryset.select_related():
            if tag.energy_accounts.exists():
                skipped += 1
                continue

            account_name = self._build_unique_account_name(
                tag.custom_label or tag.rfid
            )
            with transaction.atomic():
                account = CustomerAccount.objects.create(name=account_name)
                account.rfids.add(tag)

                updated = Transaction.objects.filter(
                    rfid__iexact=tag.rfid, account__isnull=True
                ).update(account=account)
                reassigned += updated

            created += 1

        if created:
            self.message_user(
                request,
                ngettext(
                    "Created %(count)d account from RFID selection.",
                    "Created %(count)d accounts from RFID selection.",
                    created,
                )
                % {"count": created},
                level=messages.SUCCESS,
            )

        if reassigned:
            self.message_user(
                request,
                ngettext(
                    "Linked %(count)d past transaction to the new account.",
                    "Linked %(count)d past transactions to the new accounts.",
                    reassigned,
                )
                % {"count": reassigned},
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d RFID because it is already linked to an account.",
                    "Skipped %(count)d RFIDs because they are already linked to accounts.",
                    skipped,
                )
                % {"count": skipped},
                level=messages.WARNING,
            )

    @admin.action(description=_("Toggle Released flag"))
    def toggle_selected_released(self, request, queryset):
        manager = getattr(self.model, "all_objects", self.model.objects)
        toggled = 0
        for tag in queryset:
            new_state = not tag.released
            manager.filter(pk=tag.pk).update(released=new_state)
            tag.released = new_state
            toggled += 1

        if toggled:
            self.message_user(
                request,
                ngettext(
                    "Toggled released flag for %(count)d RFID.",
                    "Toggled released flag for %(count)d RFIDs.",
                    toggled,
                )
                % {"count": toggled},
                level=messages.SUCCESS,
            )

    @admin.action(description=_("Toggle Allowed flag"))
    def toggle_selected_allowed(self, request, queryset):
        manager = getattr(self.model, "all_objects", self.model.objects)
        toggled = 0
        for tag in queryset:
            new_state = not tag.allowed
            manager.filter(pk=tag.pk).update(allowed=new_state)
            tag.allowed = new_state
            toggled += 1

        if toggled:
            self.message_user(
                request,
                ngettext(
                    "Toggled allowed flag for %(count)d RFID.",
                    "Toggled allowed flag for %(count)d RFIDs.",
                    toggled,
                )
                % {"count": toggled},
                level=messages.SUCCESS,
            )

    @admin.action(description=_("Copy RFID"))
    def copy_rfids(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                _("Select exactly one RFID to copy."),
                level=messages.ERROR,
            )
            return None

        source = (
            queryset.select_related("reference")
            .prefetch_related("energy_accounts")
            .first()
        )
        if source is None:
            self.message_user(
                request,
                _("Unable to find the selected RFID."),
                level=messages.ERROR,
            )
            return None

        if "apply" in request.POST:
            form = CopyRFIDForm(request.POST)
            if form.is_valid():
                new_rfid = form.cleaned_data["rfid"]
                label_id = RFID.next_copy_label(source)
                data_value = source.data or []
                copied_data = (
                    json.loads(json.dumps(data_value)) if data_value else []
                )
                create_kwargs = {
                    "label_id": label_id,
                    "rfid": new_rfid,
                    "custom_label": source.custom_label,
                    "key_a": source.key_a,
                    "key_b": source.key_b,
                    "key_a_verified": source.key_a_verified,
                    "key_b_verified": source.key_b_verified,
                    "allowed": source.allowed,
                    "external_command": source.external_command,
                    "post_auth_command": source.post_auth_command,
                    "color": source.color,
                    "kind": source.kind,
                    "reference": source.reference,
                    "released": source.released,
                    "data": copied_data,
                }
                try:
                    with transaction.atomic():
                        new_tag = RFID.objects.create(**create_kwargs)
                except IntegrityError:
                    form.add_error(
                        None, _("Unable to copy RFID. Please try again.")
                    )
                else:
                    new_tag.energy_accounts.set(source.energy_accounts.all())
                    self.message_user(
                        request,
                        _(
                            "Copied RFID %(source_label)s to %(new_label)s "
                            "(%(rfid)s)."
                        )
                        % {
                            "source_label": source.label_id,
                            "new_label": new_tag.label_id,
                            "rfid": new_tag.rfid,
                        },
                        level=messages.SUCCESS,
                    )
                    return HttpResponseRedirect(
                        reverse("admin:cards_rfid_change", args=[new_tag.pk])
                    )
        else:
            form = CopyRFIDForm()

        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": self.model._meta,
                "form": form,
                "source": source,
                "action": "copy_rfids",
                "title": _("Copy RFID"),
            }
        )
        context["media"] = self.media + form.media
        return TemplateResponse(request, "admin/cards/rfid/copy.html", context)

    @admin.action(description=_("Merge RFID cards"))
    def merge_rfids(self, request, queryset):
        tags = list(queryset.prefetch_related("energy_accounts"))
        if len(tags) < 2:
            self.message_user(
                request,
                _("Select at least two RFIDs to merge."),
                level=messages.WARNING,
            )
            return None

        normalized_map: dict[int, str] = {}
        groups: defaultdict[str, list[RFID]] = defaultdict(list)
        unmatched = 0
        for tag in tags:
            normalized = RFID.normalize_code(tag.rfid)
            normalized_map[tag.pk] = normalized
            if not normalized:
                unmatched += 1
                continue
            prefix = normalized[: RFID.MATCH_PREFIX_LENGTH]
            groups[prefix].append(tag)

        merge_groups: list[list[RFID]] = []
        skipped = unmatched
        for prefix, group in groups.items():
            if len(group) < 2:
                skipped += len(group)
                continue
            group.sort(
                key=lambda item: (
                    len(normalized_map.get(item.pk, "")),
                    normalized_map.get(item.pk, ""),
                    item.pk,
                )
            )
            merge_groups.append(group)

        if not merge_groups:
            self.message_user(
                request,
                _("No matching RFIDs were found to merge."),
                level=messages.WARNING,
            )
            return None

        merged_tags = 0
        merged_groups = 0
        conflicting_accounts = 0
        with transaction.atomic():
            for group in merge_groups:
                canonical = group[0]
                update_fields: set[str] = set()
                existing_account_ids = set(
                    canonical.energy_accounts.values_list("pk", flat=True)
                )
                for tag in group[1:]:
                    other_value = normalized_map.get(tag.pk, "")
                    if canonical.adopt_rfid(other_value):
                        update_fields.add("rfid")
                        normalized_map[canonical.pk] = RFID.normalize_code(
                            canonical.rfid
                        )
                    accounts = list(tag.energy_accounts.all())
                    if accounts:
                        transferable: list[CustomerAccount] = []
                        for account in accounts:
                            if existing_account_ids and account.pk not in existing_account_ids:
                                conflicting_accounts += 1
                                continue
                            transferable.append(account)
                        if transferable:
                            canonical.energy_accounts.add(*transferable)
                            existing_account_ids.update(
                                account.pk for account in transferable
                            )
                    if tag.allowed and not canonical.allowed:
                        canonical.allowed = True
                        update_fields.add("allowed")
                    if tag.released and not canonical.released:
                        canonical.released = True
                        update_fields.add("released")
                    if tag.key_a_verified and not canonical.key_a_verified:
                        canonical.key_a_verified = True
                        update_fields.add("key_a_verified")
                    if tag.key_b_verified and not canonical.key_b_verified:
                        canonical.key_b_verified = True
                        update_fields.add("key_b_verified")
                    if tag.last_seen_on and (
                        not canonical.last_seen_on
                        or tag.last_seen_on > canonical.last_seen_on
                    ):
                        canonical.last_seen_on = tag.last_seen_on
                        update_fields.add("last_seen_on")
                    if not canonical.origin_node and tag.origin_node_id:
                        canonical.origin_node = tag.origin_node
                        update_fields.add("origin_node")
                    merged_tags += 1
                    tag.delete()
                if update_fields:
                    canonical.save(update_fields=sorted(update_fields))
                merged_groups += 1

        if merged_tags:
            self.message_user(
                request,
                ngettext(
                    "Merged %(removed)d RFID into %(groups)d canonical record.",
                    "Merged %(removed)d RFIDs into %(groups)d canonical records.",
                    merged_tags,
                )
                % {"removed": merged_tags, "groups": merged_groups},
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d RFID because it did not share the first %(length)d characters with another selection.",
                    "Skipped %(count)d RFIDs because they did not share the first %(length)d characters with another selection.",
                    skipped,
                )
                % {"count": skipped, "length": RFID.MATCH_PREFIX_LENGTH},
                level=messages.WARNING,
            )

        if conflicting_accounts:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d customer account because the RFID was already linked to a different account.",
                    "Skipped %(count)d customer accounts because the RFID was already linked to a different account.",
                    conflicting_accounts,
                )
                % {"count": conflicting_accounts},
                level=messages.WARNING,
            )

    def _render_card_labels(
        self,
        request,
        queryset,
        empty_message,
        redirect_url,
    ):
        queryset = queryset.select_related("reference").order_by("label_id")
        if not queryset.exists():
            self.message_user(
                request,
                empty_message,
                level=messages.WARNING,
            )
            return HttpResponseRedirect(redirect_url)

        buffer = BytesIO()
        base_card_width = 85.6 * mm
        base_card_height = 54 * mm
        columns = 3
        rows = 4
        labels_per_page = columns * rows
        page_margin_x = 12 * mm
        page_margin_y = 12 * mm
        column_spacing = 6 * mm
        row_spacing = 6 * mm
        page_size = landscape(letter)
        page_width, page_height = page_size

        available_width = (
            page_width - (2 * page_margin_x) - (columns - 1) * column_spacing
        )
        available_height = (
            page_height - (2 * page_margin_y) - (rows - 1) * row_spacing
        )
        scale_x = available_width / (columns * base_card_width)
        scale_y = available_height / (rows * base_card_height)
        scale = min(scale_x, scale_y, 1)

        card_width = base_card_width * scale
        card_height = base_card_height * scale
        margin = 5 * mm * scale
        highlight_height = 20 * mm * scale
        content_width = card_width - 2 * margin
        left_section_width = content_width * 0.6
        right_section_width = content_width - left_section_width

        def draw_label(pdf_canvas, tag, origin_x, origin_y):
            pdf_canvas.saveState()
            pdf_canvas.translate(origin_x, origin_y)

            pdf_canvas.setFillColor(colors.white)
            pdf_canvas.rect(0, 0, card_width, card_height, stroke=0, fill=1)
            pdf_canvas.setStrokeColor(colors.HexColor("#D9D9D9"))
            pdf_canvas.setLineWidth(max(0.3, 0.5 * scale))
            pdf_canvas.rect(0, 0, card_width, card_height, stroke=1, fill=0)

            left_x = margin
            right_x = left_x + left_section_width
            highlight_bottom = card_height - margin - highlight_height

            pdf_canvas.setFillColor(colors.HexColor("#E6EEF8"))
            pdf_canvas.roundRect(
                left_x,
                highlight_bottom,
                left_section_width,
                highlight_height,
                6 * scale,
                stroke=0,
                fill=1,
            )

            pdf_canvas.setFillColor(colors.HexColor("#1A1A1A"))
            font_name = "Helvetica-Bold"
            font_size = max(6, 28 * scale)
            pdf_canvas.setFont(font_name, font_size)
            label_value = str(tag.label_id or "")
            primary_label = label_value.zfill(4) if label_value.isdigit() else label_value
            descent = abs(pdfmetrics.getDescent(font_name) / 1000 * font_size)
            vertical_center = highlight_bottom + (highlight_height / 2)
            baseline = vertical_center - (descent / 2)
            pdf_canvas.drawCentredString(
                left_x + (left_section_width / 2),
                baseline,
                primary_label,
            )

            pdf_canvas.setFont("Helvetica", max(5, 11 * scale))
            text = pdf_canvas.beginText()
            text.setTextOrigin(left_x, highlight_bottom - 16 * scale)
            text.setLeading(max(6, 14 * scale))

            details = [_("RFID: %s") % tag.rfid]
            if tag.custom_label:
                details.append(_("Custom label: %s") % tag.custom_label)
            details.append(_("Color: %s") % tag.get_color_display())
            details.append(_("Type: %s") % tag.get_kind_display())
            if tag.reference:
                details.append(_("Reference: %s") % tag.reference)

            for line in details:
                text.textLine(line)

            pdf_canvas.drawText(text)

            if tag.rfid:
                qr_code = qr.QrCodeWidget(str(tag.rfid))
                qr_bounds = qr_code.getBounds()
                qr_width = qr_bounds[2] - qr_bounds[0]
                qr_height = qr_bounds[3] - qr_bounds[1]
                qr_target_size = min(right_section_width, card_height - 2 * margin)
                if qr_width and qr_height:
                    qr_scale = qr_target_size / max(qr_width, qr_height)
                    drawing = Drawing(
                        qr_target_size,
                        qr_target_size,
                        transform=[qr_scale, 0, 0, qr_scale, 0, 0],
                    )
                    drawing.add(qr_code)
                    qr_x = right_x + (right_section_width - qr_target_size) / 2
                    qr_y = margin + (card_height - 2 * margin - qr_target_size) / 2
                    renderPDF.draw(drawing, pdf_canvas, qr_x, qr_y)

            pdf_canvas.restoreState()

        pdf = canvas.Canvas(buffer, pagesize=page_size)
        pdf.setTitle("RFID Card Labels")

        tags = list(queryset)
        total_tags = len(tags)

        for page_start in range(0, total_tags, labels_per_page):
            pdf.setPageSize(page_size)
            pdf.setFillColor(colors.white)
            pdf.rect(0, 0, page_width, page_height, stroke=0, fill=1)
            subset = tags[page_start : page_start + labels_per_page]

            for index, tag in enumerate(subset):
                column = index % columns
                row = index // columns
                x = page_margin_x + column * (card_width + column_spacing)
                y = (
                    page_height
                    - page_margin_y
                    - card_height
                    - row * (card_height + row_spacing)
                )
                draw_label(pdf, tag, x, y)

            pdf.showPage()

        pdf.save()
        buffer.seek(0)

        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = "attachment; filename=rfid-card-labels.pdf"
        return response

    def print_card_labels(self, request, queryset):
        return self._render_card_labels(
            request,
            queryset,
            _("Select at least one RFID to print labels."),
            request.get_full_path(),
        )

    print_card_labels.short_description = _("Print Card Labels")

    def _render_release_form(self, request, queryset, empty_message, redirect_url):
        tags = list(queryset)
        if not tags:
            self.message_user(request, empty_message, level=messages.WARNING)
            return HttpResponseRedirect(redirect_url)

        language = getattr(request, "LANGUAGE_CODE", translation.get_language())
        if not language:
            language = settings.LANGUAGE_CODE

        with translation.override(language):
            buffer = BytesIO()
            document = SimpleDocTemplate(
                buffer,
                pagesize=letter,
                leftMargin=36,
                rightMargin=36,
                topMargin=72,
                bottomMargin=36,
            )
            document.title = str(_("RFID Release Form"))

            styles = getSampleStyleSheet()
            story = []
            story.append(Paragraph(_("RFID Release Form"), styles["Title"]))
            story.append(Spacer(1, 12))

            generated_on = timezone.localtime()
            formatted_generated_on = date_format(generated_on, "DATETIME_FORMAT")
            if generated_on.tzinfo:
                formatted_generated_on = _("%(datetime)s %(timezone)s") % {
                    "datetime": formatted_generated_on,
                    "timezone": generated_on.tzname() or "",
                }
            generated_text = Paragraph(
                _("Generated on: %(date)s")
                % {"date": formatted_generated_on},
                styles["Normal"],
            )
            story.append(generated_text)
            story.append(Spacer(1, 24))

            table_data = [
                [
                    _("Label"),
                    _("RFID"),
                    _("Custom label"),
                    _("Color"),
                    _("Type"),
                ]
            ]

            for tag in tags:
                table_data.append(
                    [
                        tag.label_id or "",
                        tag.rfid or "",
                        tag.custom_label or "",
                        tag.get_color_display() if tag.color else "",
                        tag.get_kind_display() if tag.kind else "",
                    ]
                )

            table = Table(table_data, repeatRows=1, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ]
                )
            )

            story.append(table)
            story.append(Spacer(1, 36))

            signature_lines = [
                [
                    Paragraph(
                        _("Issuer Signature: ______________________________"),
                        styles["Normal"],
                    ),
                    Paragraph(
                        _("Receiver Signature: ______________________________"),
                        styles["Normal"],
                    ),
                ],
                [
                    Paragraph(
                        _("Issuer Name: ______________________________"),
                        styles["Normal"],
                    ),
                    Paragraph(
                        _("Receiver Name: ______________________________"),
                        styles["Normal"],
                    ),
                ],
            ]

            signature_table = Table(
                signature_lines,
                colWidths=[document.width / 2.0, document.width / 2.0],
                hAlign="LEFT",
            )
            signature_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ]
                )
            )
            story.append(signature_table)

            document.build(story)
            buffer.seek(0)

            response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
            response["Content-Disposition"] = "attachment; filename=rfid-release-form.pdf"
            return response

    def print_release_form(self, request, queryset):
        return self._render_release_form(
            request,
            queryset,
            _("Select at least one RFID to print the release form."),
            request.get_full_path(),
        )

    print_release_form.short_description = _("Print Release Form")

    def get_changelist_actions(self, request):
        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        actions.append("print_valid_card_labels")
        return actions

    def print_valid_card_labels(self, request):
        queryset = self.get_queryset(request).filter(allowed=True, released=True)
        changelist_url = reverse("admin:cards_rfid_changelist")
        return self._render_card_labels(
            request,
            queryset,
            _("No RFID cards marked as valid are available to print."),
            changelist_url,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "report/",
                self.admin_site.admin_view(self.report_view),
                name="cards_rfid_report",
            ),
            path(
                "print-valid-labels/",
                self.admin_site.admin_view(self.print_valid_card_labels),
                name="cards_rfid_print_valid_card_labels",
            ),
            path(
                "scan/",
                self.admin_site.admin_view(csrf_exempt(self.scan_view)),
                name="cards_rfid_scan",
            ),
            path(
                "scan/next/",
                self.admin_site.admin_view(csrf_exempt(self.scan_next)),
                name="cards_rfid_scan_next",
            ),
        ]
        return custom + urls

    def report_view(self, request):
        context = self.admin_site.each_context(request)
        context["report"] = ClientReport.build_rows(for_display=True)
        return TemplateResponse(request, "admin/cards/rfid/report.html", context)

    def scan_view(self, request):
        context = self.admin_site.each_context(request)
        table_mode, toggle_url, toggle_label = build_mode_toggle(request)
        public_view_url = reverse("rfid-reader")
        if table_mode:
            public_view_url = f"{public_view_url}?mode=table"
        context.update(
            {
                "scan_url": reverse("admin:cards_rfid_scan_next"),
                "admin_change_url_template": reverse(
                    "admin:cards_rfid_change", args=[0]
                ),
                "title": _("Scan RFIDs"),
                "opts": self.model._meta,
                "table_mode": table_mode,
                "toggle_url": toggle_url,
                "toggle_label": toggle_label,
                "public_view_url": public_view_url,
                "deep_read_url": reverse("rfid-scan-deep"),
            }
        )
        context["title"] = _("Scan RFIDs")
        context["opts"] = self.model._meta
        context["show_release_info"] = True
        context["default_endianness"] = RFID.BIG_ENDIAN
        return render(request, "admin/cards/rfid/scan.html", context)

    def scan_next(self, request):
        from apps.cards.scanner import scan_sources
        from apps.cards.reader import validate_rfid_value

        if request.method == "POST":
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JsonResponse({"error": "Invalid JSON payload"}, status=400)
            rfid = payload.get("rfid") or payload.get("value")
            kind = payload.get("kind")
            endianness = payload.get("endianness")
            result = validate_rfid_value(rfid, kind=kind, endianness=endianness)
        else:
            endianness = request.GET.get("endianness")
            result = scan_sources(request, endianness=endianness)
        status = 500 if result.get("error") else 200
        return JsonResponse(result, status=status)
