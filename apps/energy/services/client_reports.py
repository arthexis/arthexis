from __future__ import annotations

import calendar
import datetime
import re
from dataclasses import dataclass
from typing import Any, Iterable

from apps.energy.models import ClientReport, ClientReportSchedule


@dataclass
class ClientReportResult:
    report: ClientReport
    schedule: ClientReportSchedule | None
    rows: dict[str, Any]
    delivered_recipients: list[str]


def resolve_client_report_window(
    period: str | None,
    *,
    start_date: datetime.date | datetime.datetime | None = None,
    end_date: datetime.date | datetime.datetime | None = None,
    week: str | None = None,
    month: str | datetime.date | datetime.datetime | None = None,
) -> tuple[datetime.date, datetime.date]:
    if isinstance(start_date, datetime.datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime.datetime):
        end_date = end_date.date()

    if period == "week":
        if not week:
            raise ValueError("Week value required for weekly reports.")
        match = re.match(r"^(?P<year>\d{4})-W?(?P<week>\d{1,2})$", week)
        if not match:
            raise ValueError("Week value must be in YYYY-Www format.")
        start = datetime.date.fromisocalendar(
            int(match.group("year")), int(match.group("week")), 1
        )
        end = start + datetime.timedelta(days=6)
        return start, end

    if period == "month":
        if month is None:
            raise ValueError("Month value required for monthly reports.")
        if isinstance(month, datetime.datetime):
            month_date = month.date()
        elif isinstance(month, datetime.date):
            month_date = month
        else:
            match = re.match(r"^(?P<year>\d{4})-(?P<month>\d{2})$", month)
            if not match:
                raise ValueError("Month value must be in YYYY-MM format.")
            month_date = datetime.date(
                int(match.group("year")), int(match.group("month")), 1
            )
        start = month_date.replace(day=1)
        last_day = calendar.monthrange(month_date.year, month_date.month)[1]
        end = month_date.replace(day=last_day)
        return start, end

    if start_date is None or end_date is None:
        raise ValueError("Start and end dates are required for ranged reports.")
    return start_date, end_date


def create_client_report(
    *,
    period: str | None,
    start_date: datetime.date | datetime.datetime | None = None,
    end_date: datetime.date | datetime.datetime | None = None,
    week: str | None = None,
    month: str | datetime.date | datetime.datetime | None = None,
    owner=None,
    created_by=None,
    recipients: Iterable[str] | None = None,
    chargers: Iterable[Any] | None = None,
    language: str | None = None,
    title: str | None = None,
    recurrence: str | None = None,
    send_emails: bool = True,
    store_local_copy: bool = False,
) -> ClientReportResult:
    start_date, end_date = resolve_client_report_window(
        period,
        start_date=start_date,
        end_date=end_date,
        week=week,
        month=month,
    )
    charger_list = list(chargers or [])
    recipient_list = list(recipients or [])
    disable_emails = not send_emails
    report = ClientReport.generate(
        start_date,
        end_date,
        owner=owner,
        recipients=recipient_list,
        disable_emails=disable_emails,
        chargers=charger_list,
        language=language,
        title=title,
    )

    if store_local_copy:
        report.store_local_copy()

    delivered_recipients: list[str] = []
    if send_emails and recipient_list:
        delivered_recipients = report.send_delivery(
            to=recipient_list,
            cc=[],
            outbox=ClientReport.resolve_outbox_for_owner(owner),
            reply_to=ClientReport.resolve_reply_to_for_owner(owner),
        )
        if delivered_recipients:
            report.recipients = delivered_recipients
            report.save(update_fields=["recipients"])

    schedule = None
    if recurrence and recurrence != ClientReportSchedule.PERIODICITY_NONE:
        schedule = ClientReportSchedule.objects.create(
            owner=owner,
            created_by=created_by,
            periodicity=recurrence,
            email_recipients=recipient_list,
            disable_emails=disable_emails,
            language=language,
            title=title,
        )
        if charger_list:
            schedule.chargers.set(charger_list)
        report.schedule = schedule
        report.save(update_fields=["schedule"])

    rows = report.rows_for_display
    return ClientReportResult(
        report=report,
        schedule=schedule,
        rows=rows,
        delivered_recipients=delivered_recipients,
    )
