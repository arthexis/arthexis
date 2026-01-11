import calendar
import datetime
from dataclasses import dataclass, field
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.emails.utils import normalize_recipients
from apps.energy.models import ClientReport, ClientReportSchedule
from apps.energy.services.client_reports import create_client_report
from apps.ocpp.models import Charger


class ClientReportForm(forms.Form):
    PERIOD_CHOICES = [
        ("range", _("Date range")),
        ("week", _("Week")),
        ("month", _("Month")),
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
        help_text=_("Choose how the reporting window will be calculated."),
    )
    start = forms.DateField(
        label=_("Start date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("First day included when using a custom date range."),
    )
    end = forms.DateField(
        label=_("End date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Last day included when using a custom date range."),
    )
    week = forms.CharField(
        label=_("Week"),
        required=False,
        widget=forms.TextInput(attrs={"type": "week"}),
        help_text=_("Generates the report for the ISO week that you select."),
    )
    month = forms.DateField(
        label=_("Month"),
        required=False,
        widget=forms.DateInput(attrs={"type": "month"}),
        input_formats=["%Y-%m"],
        help_text=_("Generates the report for the calendar month that you select."),
    )
    view_mode = forms.ChoiceField(
        label=_("Report layout"),
        choices=VIEW_CHOICES,
        initial="expanded",
        widget=forms.RadioSelect,
        help_text=_(
            "Choose between detailed charge point sections or a combined summary table."
        ),
    )
    language = forms.ChoiceField(
        label=_("Report language"),
        choices=settings.LANGUAGES,
        help_text=_("Choose the language used for the generated report."),
    )
    title = forms.CharField(
        label=_("Report title"),
        required=False,
        max_length=200,
        help_text=_("Optional heading that replaces the default report title."),
    )
    chargers = forms.ModelMultipleChoiceField(
        label=_("Charge points"),
        queryset=Charger.objects.filter(connector_id__isnull=True).order_by(
            "display_name", "charger_id"
        ),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Choose which charge points are included in the report."),
    )
    owner = forms.ModelChoiceField(
        queryset=get_user_model().objects.all(),
        required=False,
        help_text=_(
            "Sets who owns the report schedule and is listed as the requester."
        ),
    )
    destinations = forms.CharField(
        label=_("Email destinations"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Separate addresses with commas, whitespace, or new lines."),
    )
    recurrence = forms.ChoiceField(
        label=_("Recurrence"),
        choices=RECURRENCE_CHOICES,
        initial=ClientReportSchedule.PERIODICITY_NONE,
        help_text=_("Defines how often the report should be generated automatically."),
    )
    enable_emails = forms.BooleanField(
        label=_("Enable email delivery"),
        required=False,
        help_text=_("Send the report via email to the recipients listed above."),
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            self.fields["owner"].initial = request.user.pk
        self.fields["chargers"].widget.attrs["class"] = "charger-options"
        if not self.is_bound:
            queryset = self.fields["chargers"].queryset
            self.fields["chargers"].initial = list(
                queryset.values_list("pk", flat=True)
            )
        language_initial = ClientReport.default_language()
        if request:
            language_initial = ClientReport.normalize_language(
                getattr(request, "LANGUAGE_CODE", language_initial)
            )
        self.fields["language"].initial = language_initial

    def clean(self):
        cleaned = super().clean()
        period = cleaned.get("period")
        if period == "range":
            if not cleaned.get("start") or not cleaned.get("end"):
                raise forms.ValidationError(_("Please provide start and end dates."))
        elif period == "week":
            week_str = cleaned.get("week")
            if not week_str:
                raise forms.ValidationError(_("Please select a week."))
            try:
                year_str, week_num_str = week_str.split("-W", 1)
                start = datetime.date.fromisocalendar(
                    int(year_str), int(week_num_str), 1
                )
            except (TypeError, ValueError):
                raise forms.ValidationError(_("Please select a week."))
            cleaned["start"] = start
            cleaned["end"] = start + datetime.timedelta(days=6)
        elif period == "month":
            month_dt = cleaned.get("month")
            if not month_dt:
                raise forms.ValidationError(_("Please select a month."))
            start = month_dt.replace(day=1)
            last_day = calendar.monthrange(month_dt.year, month_dt.month)[1]
            cleaned["start"] = start
            cleaned["end"] = month_dt.replace(day=last_day)
        return cleaned

    def clean_destinations(self):
        raw = self.cleaned_data.get("destinations", "")
        return normalize_recipients(raw, validate=True)

    def clean_title(self):
        title = self.cleaned_data.get("title")
        return ClientReport.normalize_title(title)


def client_report(request):
    form = ClientReportForm(request.POST or None, request=request)
    report = None
    schedule = None
    report_rows = None
    report_summary_rows: list[dict[str, Any]] = []
    if request.method == "POST":
        post_result = _process_client_report_post(request, form)
        if post_result.response is not None:
            return post_result.response
        report = post_result.report
        schedule = post_result.schedule
        report_rows = post_result.report_rows
        report_summary_rows = post_result.report_summary_rows
    download_url = None
    download_param = request.GET.get("download")
    if download_param:
        try:
            download_id = int(download_param)
        except (TypeError, ValueError):
            download_id = None
        if download_id and request.user.is_authenticated:
            download_url = reverse("pages:client-report-download", args=[download_id])

    try:
        login_url = reverse("pages:login")
    except NoReverseMatch:
        try:
            login_url = reverse("login")
        except NoReverseMatch:
            login_url = getattr(settings, "LOGIN_URL", None)

    if report and report_rows is None:
        report_rows = report.rows_for_display
        report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)

    selected_view_mode = form.fields["view_mode"].initial
    if form.is_bound:
        if form.is_valid():
            selected_view_mode = form.cleaned_data.get("view_mode", selected_view_mode)
        else:
            selected_view_mode = form.data.get("view_mode", selected_view_mode)

    context = {
        "form": form,
        "report": report,
        "schedule": schedule,
        "login_url": login_url,
        "download_url": download_url,
        "report_rows": report_rows,
        "report_summary_rows": report_summary_rows,
        "report_view_mode": selected_view_mode,
    }
    return render(request, "pages/client_report.html", context)


@dataclass
class _ClientReportPostResult:
    report: ClientReport | None = None
    schedule: ClientReportSchedule | None = None
    report_rows: list[dict[str, Any]] | None = None
    report_summary_rows: list[dict[str, Any]] = field(default_factory=list)
    response: HttpResponseRedirect | None = None


def _process_client_report_post(
    request, form: "ClientReportForm"
) -> _ClientReportPostResult:
    if not request.user.is_authenticated:
        # Run validation to surface field errors alongside auth error.
        form.is_valid()
        form.add_error(None, _("You must log in to generate consumer reports."))
        return _ClientReportPostResult()

    if not form.is_valid():
        return _ClientReportPostResult()

    throttle_error = _enforce_client_report_throttle(request)
    if throttle_error:
        form.add_error(None, throttle_error)
        return _ClientReportPostResult()

    return _generate_client_report_response(request, form)


def _enforce_client_report_throttle(request) -> str | None:
    throttle_seconds = getattr(settings, "CLIENT_REPORT_THROTTLE_SECONDS", 60)
    if not throttle_seconds:
        return None

    throttle_keys = _build_client_report_throttle_keys(request)
    added_keys: list[str] = []
    for key in throttle_keys:
        if cache.add(key, timezone.now(), throttle_seconds):
            added_keys.append(key)
            continue
        for added_key in added_keys:
            cache.delete(added_key)
        return _(
            "Consumer reports can only be generated periodically. Please wait before trying again."
        )
    return None


def _build_client_report_throttle_keys(request) -> list[str]:
    keys: list[str] = []
    if request.user.is_authenticated:
        keys.append(f"client-report:user:{request.user.pk}")

    remote_addr = request.META.get("HTTP_X_FORWARDED_FOR")
    if remote_addr:
        remote_addr = remote_addr.split(",")[0].strip()
    remote_addr = remote_addr or request.META.get("REMOTE_ADDR")
    if remote_addr:
        keys.append(f"client-report:ip:{remote_addr}")
    return keys


def _generate_client_report_response(
    request, form: "ClientReportForm"
) -> _ClientReportPostResult:
    owner = _resolve_client_report_owner(request, form)
    enable_emails = form.cleaned_data.get("enable_emails", False)
    disable_emails = not enable_emails
    recipients = form.cleaned_data.get("destinations") if enable_emails else []
    chargers = list(form.cleaned_data.get("chargers") or [])
    language = form.cleaned_data.get("language")
    title = form.cleaned_data.get("title")
    recurrence = form.cleaned_data.get("recurrence")
    result = create_client_report(
        period=form.cleaned_data.get("period"),
        start_date=form.cleaned_data.get("start"),
        end_date=form.cleaned_data.get("end"),
        week=form.cleaned_data.get("week"),
        month=form.cleaned_data.get("month"),
        owner=owner,
        created_by=request.user if request.user.is_authenticated else None,
        recipients=recipients,
        chargers=chargers,
        language=language,
        title=title,
        recurrence=recurrence,
        send_emails=enable_emails,
        store_local_copy=True,
    )
    report = result.report
    schedule = result.schedule

    if result.delivered_recipients:
        messages.success(
            request,
            _("Consumer report emailed to the selected recipients."),
        )
    if schedule:
        messages.success(
            request,
            _(
                "Consumer report schedule created; future reports will be generated automatically."
            ),
        )

    if disable_emails:
        messages.success(
            request,
            _("Consumer report generated. The download will begin automatically."),
        )
        redirect_url = f"{reverse('pages:client-report')}?download={report.pk}"
        return _ClientReportPostResult(
            report=report,
            schedule=schedule,
            response=HttpResponseRedirect(redirect_url),
        )

    report_rows = result.rows
    report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)
    return _ClientReportPostResult(
        report=report,
        schedule=schedule,
        report_rows=report_rows,
        report_summary_rows=report_summary_rows,
    )


def _resolve_client_report_owner(request, form: "ClientReportForm"):
    owner = form.cleaned_data.get("owner")
    if not owner and request.user.is_authenticated:
        return request.user
    return owner


@login_required
def client_report_download(request, report_id: int):
    report = get_object_or_404(ClientReport, pk=report_id)
    if not request.user.is_staff and report.owner_id != request.user.pk:
        return HttpResponseForbidden(
            _("You do not have permission to download this report.")
        )
    pdf_path = report.ensure_pdf()
    if not pdf_path.exists():
        raise Http404(_("Report file unavailable."))
    filename = f"consumer-report-{report.start_date}-{report.end_date}.pdf"
    response = FileResponse(pdf_path.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
