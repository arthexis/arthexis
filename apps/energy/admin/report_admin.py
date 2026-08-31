from __future__ import annotations

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.emails.utils import normalize_recipients
from apps.locals.user_data import EntityModelAdmin
from apps.ocpp.models import Charger

from ..models import ClientReport, ClientReportSchedule
from ..services.client_reports import create_client_report


class ClientReportRecurrencyFilter(admin.SimpleListFilter):
    title = "Recurrency"
    parameter_name = "recurrency"

    def lookups(self, request, model_admin):
        for value, label in ClientReportSchedule.PERIODICITY_CHOICES:
            yield (value, label)

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if value == ClientReportSchedule.PERIODICITY_NONE:
            return queryset.filter(
                Q(schedule__isnull=True) | Q(schedule__periodicity=value)
            )
        return queryset.filter(schedule__periodicity=value)


@admin.register(ClientReport)
class ClientReportAdmin(EntityModelAdmin):
    list_display = (
        "created_on",
        "period_range",
        "owner",
        "recurrency_display",
        "total_kw_period_display",
        "download_link",
    )
    list_select_related = ("schedule", "owner")
    list_filter = ("owner", ClientReportRecurrencyFilter)
    readonly_fields = ("created_on", "data")

    change_list_template = "admin/core/clientreport/change_list.html"

    def period_range(self, obj):
        return str(obj)

    period_range.short_description = "Period"

    def recurrency_display(self, obj):
        return obj.periodicity_label

    recurrency_display.short_description = "Recurrency"

    def total_kw_period_display(self, obj):
        return f"{obj.total_kw_period:.2f}"

    total_kw_period_display.short_description = "Total kW (period)"

    def download_link(self, obj):
        url = reverse("admin:core_clientreport_download", args=[obj.pk])
        return format_html('<a href="{}">Download</a>', url)

    download_link.short_description = "Download"

    class ClientReportForm(forms.Form):
        PERIOD_CHOICES = [
            ("range", "Date range"),
            ("week", "Week"),
            ("month", "Month"),
        ]
        RECURRENCE_CHOICES = ClientReportSchedule.PERIODICITY_CHOICES
        VIEW_CHOICES = [
            ("expanded", _("Expanded view")),
            ("summary", _("Summarized view")),
        ]
        period = forms.ChoiceField(
            choices=PERIOD_CHOICES,
            widget=forms.RadioSelect,
            initial="range",
            help_text="Choose how the reporting window will be calculated.",
        )
        start_date = forms.DateField(required=False)
        end_date = forms.DateField(required=False)
        week = forms.CharField(required=False, help_text="yyyy-ww")
        month = forms.CharField(required=False, help_text="yyyy-mm")
        chargers = forms.ModelMultipleChoiceField(
            queryset=Charger.objects.all(),
            widget=forms.SelectMultiple,
            required=False,
        )
        recurrence = forms.ChoiceField(
            choices=RECURRENCE_CHOICES,
            required=False,
            initial=ClientReportSchedule.PERIODICITY_NONE,
            help_text="Select a cadence to automatically email new reports.",
        )
        email_recipients = forms.CharField(
            required=False,
            widget=forms.Textarea,
            help_text="Optional comma-separated email list for reports.",
        )
        disable_emails = forms.BooleanField(
            required=False,
            help_text="Generate the report without emailing recipients.",
        )
        title = forms.CharField(required=False, max_length=200)
        view_mode = forms.ChoiceField(
            choices=VIEW_CHOICES,
            required=False,
            initial="expanded",
            widget=forms.RadioSelect,
        )
        language = forms.ChoiceField(
            choices=settings.LANGUAGES,
            required=False,
            initial=ClientReport.default_language(),
        )

        def clean_week(self):
            week = self.cleaned_data["week"]
            if week:
                return ClientReport.normalize_week(week)
            return week

        def clean_month(self):
            month = self.cleaned_data["month"]
            if month:
                return ClientReport.normalize_month(month)
            return month

        def clean_title(self):
            title = self.cleaned_data.get("title")
            if not title:
                return ClientReport.default_title()
            return ClientReport.normalize_title(title)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "generate/",
                self.admin_site.admin_view(self.generate_view),
                name="core_clientreport_generate",
            ),
            path(
                "download/<int:report_id>/",
                self.admin_site.admin_view(self.download_view),
                name="core_clientreport_download",
            ),
        ]
        return custom + urls

    def generate_view(self, request):
        report = None
        report_rows = None
        schedule = None
        download_url = None
        form = self.ClientReportForm(request.POST or None)
        selected_chargers = Charger.objects.none()
        if form.is_bound and form.is_valid():
            chargers = form.cleaned_data.get("chargers")
            selected_chargers = (
                chargers if chargers is not None else Charger.objects.none()
            )
            recipients_raw = form.cleaned_data.get("email_recipients") or ""
            recipients = normalize_recipients(recipients_raw)
            disable_emails = form.cleaned_data.get("disable_emails")
            title = form.cleaned_data.get("title")
            language = form.cleaned_data.get("language")
            user = request.user if request.user.is_authenticated else None
            recurrence = form.cleaned_data.get("recurrence")
            result = create_client_report(
                period=form.cleaned_data.get("period"),
                start_date=form.cleaned_data.get("start_date"),
                end_date=form.cleaned_data.get("end_date"),
                week=form.cleaned_data.get("week"),
                month=form.cleaned_data.get("month"),
                owner=user,
                created_by=user,
                recipients=recipients,
                chargers=chargers,
                language=language,
                title=title,
                recurrence=recurrence,
                send_emails=not disable_emails,
            )
            report = result.report
            report_rows = result.rows
            schedule = result.schedule
            if result.delivered_recipients:
                self.message_user(
                    request,
                    "Consumer report emailed to the selected recipients.",
                    messages.SUCCESS,
                )
            if schedule:
                self.message_user(
                    request,
                    "Consumer report schedule created; future reports will be generated automatically.",
                    messages.SUCCESS,
                )
            if disable_emails:
                self.message_user(
                    request,
                    "Consumer report generated. The download will begin automatically.",
                    messages.SUCCESS,
                )
                redirect_url = f"{reverse('admin:core_clientreport_generate')}?download={report.pk}"
                return HttpResponseRedirect(redirect_url)
            report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)
        else:
            report_summary_rows = None
            if form.is_bound:
                selected_chargers = form.cleaned_data.get("chargers") or Charger.objects.none()

        download_param = request.GET.get("download")
        if download_param:
            try:
                download_report = ClientReport.objects.get(pk=download_param)
            except ClientReport.DoesNotExist:
                pass
            else:
                download_url = reverse(
                    "admin:core_clientreport_download", args=[download_report.pk]
                )
        if report and report_rows is None:
            report_rows = report.rows_for_display
            report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)
        selected_view_mode = form.fields["view_mode"].initial
        if form.is_bound:
            if form.is_valid():
                selected_view_mode = form.cleaned_data.get(
                    "view_mode", selected_view_mode
                )
            else:
                selected_view_mode = form.data.get("view_mode", selected_view_mode)
        context = self.admin_site.each_context(request)
        context.update(
            {
                "form": form,
                "report": report,
                "schedule": schedule,
                "download_url": download_url,
                "opts": self.model._meta,
                "report_rows": report_rows,
                "report_summary_rows": report_summary_rows,
                "report_view_mode": selected_view_mode,
                "selected_chargers": selected_chargers,
            }
        )
        return TemplateResponse(
            request, "admin/core/clientreport/generate.html", context
        )

    def get_changelist_actions(self, request):
        parent = getattr(super(), "get_changelist_actions", None)
        actions: list[str] = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        if "generate_report" not in actions:
            actions.append("generate_report")
        return actions

    def generate_report(self, request):
        return HttpResponseRedirect(reverse("admin:core_clientreport_generate"))

    generate_report.label = _("Generate report")

    def download_view(self, request, report_id: int):
        report = get_object_or_404(ClientReport, pk=report_id)
        pdf_path = report.ensure_pdf()
        if not pdf_path.exists():
            raise Http404("Report file unavailable")
        end_date = report.end_date
        if hasattr(end_date, "isoformat"):
            end_date_str = end_date.isoformat()
        else:  # pragma: no cover - fallback for unexpected values
            end_date_str = str(end_date)
        filename = f"consumer-report-{end_date_str}.pdf"
        response = FileResponse(pdf_path.open("rb"), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
