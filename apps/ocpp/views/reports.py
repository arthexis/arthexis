from __future__ import annotations

import csv
from datetime import datetime, time, timedelta

from django import forms
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.groups.decorators import staff_required
from apps.sites.utils import landing, module_pill_link_validation

from ..models import Charger, Transaction, annotate_transaction_energy_bounds


class EnergyReportForm(forms.Form):
    start = forms.DateField(
        label=_("Start date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end = forms.DateField(
        label=_("End date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start")
        end = cleaned_data.get("end")
        if start and end and start > end:
            raise forms.ValidationError(_("Start date must be before end date."))
        return cleaned_data


def _default_report_range():
    end = timezone.localdate()
    return end - timedelta(days=30), end


def _date_start(value):
    dt = datetime.combine(value, time.min)
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _date_end(value):
    return _date_start(value + timedelta(days=1))


def _landing_requires_staff(*, request, landing=None) -> bool:
    del landing
    user = getattr(request, "user", None)
    return bool(
        getattr(user, "is_authenticated", False)
        and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    )


def _format_dt(value):
    if value is None:
        return ""
    return timezone.localtime(value).isoformat()


def _sanitize_csv_value(value):
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _chargers_reportable_by_user(user):
    chargers = Charger.objects.all()
    if getattr(user, "is_superuser", False) or Charger._user_is_charge_station_manager(
        user
    ):
        return chargers
    if not getattr(user, "is_authenticated", False):
        return chargers.filter(
            owner_users__isnull=True, owner_groups__isnull=True
        ).distinct()
    group_ids = list(user.groups.values_list("pk", flat=True))
    visibility = Q(owner_users__isnull=True, owner_groups__isnull=True) | Q(
        owner_users=user
    )
    if group_ids:
        visibility |= Q(owner_groups__pk__in=group_ids)
    return chargers.filter(visibility).distinct()


def _energy_report_response(*, user, start, end):
    start_dt = _date_start(start)
    end_dt = _date_end(end)
    transactions = annotate_transaction_energy_bounds(
        Transaction.objects.select_related("charger")
        .filter(
            charger__in=_chargers_reportable_by_user(user),
            start_time__gte=start_dt,
            start_time__lt=end_dt,
        )
        .order_by("start_time", "pk"),
        start_field="report_meter_energy_start",
        end_field="report_meter_energy_end",
    )

    filename = f"charger-energy-{start:%Y%m%d}-{end:%Y%m%d}.csv"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "charger_id",
            "connector_id",
            "transaction_id",
            "ocpp_transaction_id",
            "start_time",
            "stop_time",
            "meter_start",
            "meter_stop",
            "energy_kwh",
            "rfid",
            "vehicle_identifier",
        ]
    )
    for tx in transactions:
        charger = tx.charger
        writer.writerow(
            [
                _sanitize_csv_value(charger.charger_id if charger else ""),
                tx.connector_id if tx.connector_id is not None else "",
                tx.pk,
                _sanitize_csv_value(tx.ocpp_transaction_id),
                _format_dt(tx.start_time),
                _format_dt(tx.stop_time),
                tx.meter_start if tx.meter_start is not None else "",
                tx.meter_stop if tx.meter_stop is not None else "",
                f"{tx.kw:.3f}",
                _sanitize_csv_value(tx.rfid),
                _sanitize_csv_value(tx.vehicle_identifier),
            ]
        )
    return response


@landing("Energy Reports")
@module_pill_link_validation(_landing_requires_staff)
@staff_required
def energy_reports(request):
    start, end = _default_report_range()
    initial = {"start": start, "end": end}
    form = EnergyReportForm(request.GET or None, initial=initial)

    if request.GET.get("download") == "1":
        if form.is_valid():
            return _energy_report_response(
                user=request.user,
                start=form.cleaned_data["start"],
                end=form.cleaned_data["end"],
            )
        return render(
            request,
            "ocpp/energy_reports.html",
            {"form": form},
            status=400,
        )

    return render(request, "ocpp/energy_reports.html", {"form": form})
