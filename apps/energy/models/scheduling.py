from __future__ import annotations

import json as _json
from datetime import date as datetime_date

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext

from apps.celery.utils import normalize_periodic_task_name
from apps.emails.utils import resolve_recipient_fallbacks
from apps.core.entity import Entity
from apps.locale.language import (
    default_report_language,
    normalize_report_language,
    normalize_report_title,
)


class ClientReportSchedule(Entity):
    """Configuration for recurring :class:`ClientReport` generation."""

    PERIODICITY_NONE = "none"
    PERIODICITY_DAILY = "daily"
    PERIODICITY_WEEKLY = "weekly"
    PERIODICITY_MONTHLY = "monthly"
    PERIODICITY_BIMONTHLY = "bimonthly"
    PERIODICITY_QUARTERLY = "quarterly"
    PERIODICITY_YEARLY = "yearly"
    PERIODICITY_CHOICES = [
        (PERIODICITY_NONE, "One-time"),
        (PERIODICITY_DAILY, "Daily"),
        (PERIODICITY_WEEKLY, "Weekly"),
        (PERIODICITY_MONTHLY, "Monthly"),
        (PERIODICITY_BIMONTHLY, "Bi-monthly (2 months)"),
        (PERIODICITY_QUARTERLY, "Quarterly"),
        (PERIODICITY_YEARLY, "Yearly"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_report_schedules",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_client_report_schedules",
    )
    periodicity = models.CharField(
        max_length=12, choices=PERIODICITY_CHOICES, default=PERIODICITY_NONE
    )
    language = models.CharField(
        max_length=12,
        choices=settings.LANGUAGES,
        default=default_report_language,
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name=gettext("Title"),
    )
    email_recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)
    chargers = models.ManyToManyField(
        "ocpp.Charger",
        blank=True,
        related_name="client_report_schedules",
    )
    periodic_task = models.OneToOneField(
        "django_celery_beat.PeriodicTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_report_schedule",
    )
    last_generated_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Client Report Schedule"
        verbose_name_plural = "Client Report Schedules"
        db_table = "core_clientreportschedule"

    @classmethod
    def label_for_periodicity(cls, value: str) -> str:
        lookup = dict(cls.PERIODICITY_CHOICES)
        return lookup.get(value, value)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        owner = self.owner.get_username() if self.owner else "Unassigned"
        return f"Client Report Schedule ({owner})"

    def save(self, *args, **kwargs):
        if self.language:
            self.language = normalize_report_language(self.language)
        self.title = normalize_report_title(self.title)
        sync = kwargs.pop("sync_task", True)
        super().save(*args, **kwargs)
        if sync and self.pk:
            self.sync_periodic_task()

    def delete(self, using=None, keep_parents=False):
        task_id = self.periodic_task_id
        super().delete(using=using, keep_parents=keep_parents)
        if task_id:
            from django_celery_beat.models import PeriodicTask

            PeriodicTask.objects.filter(pk=task_id).delete()

    def sync_periodic_task(self):
        """Ensure the Celery beat schedule matches the configured periodicity."""

        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        if self.periodicity == self.PERIODICITY_NONE:
            if self.periodic_task_id:
                PeriodicTask.objects.filter(pk=self.periodic_task_id).delete()
                type(self).objects.filter(pk=self.pk).update(periodic_task=None)
            return

        if self.periodicity == self.PERIODICITY_DAILY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="2",
                day_of_week="*",
                day_of_month="*",
                month_of_year="*",
            )
        elif self.periodicity == self.PERIODICITY_WEEKLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="3",
                day_of_week="1",
                day_of_month="*",
                month_of_year="*",
            )
        elif self.periodicity == self.PERIODICITY_MONTHLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="*",
            )
        elif self.periodicity == self.PERIODICITY_BIMONTHLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="1,3,5,7,9,11",
            )
        elif self.periodicity == self.PERIODICITY_QUARTERLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="1,4,7,10",
            )
        else:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="1",
            )

        raw_name = f"client_report_schedule_{self.pk}"
        name = normalize_periodic_task_name(PeriodicTask.objects, raw_name)
        defaults = {
            "crontab": schedule,
            "task": "apps.core.tasks.run_client_report_schedule",
            "kwargs": _json.dumps({"schedule_id": self.pk}),
            "enabled": True,
        }
        with transaction.atomic():
            periodic_task, _ = PeriodicTask.objects.update_or_create(
                name=name, defaults=defaults
            )
            if self.periodic_task_id != periodic_task.pk:
                type(self).objects.filter(pk=self.pk).update(
                    periodic_task=periodic_task
                )

    def calculate_period(self, reference=None):
        """Return the date range covered for the next execution."""

        import datetime as _datetime

        ref_date = reference or timezone.localdate()

        if self.periodicity == self.PERIODICITY_DAILY:
            end = ref_date - _datetime.timedelta(days=1)
            start = end
        elif self.periodicity == self.PERIODICITY_WEEKLY:
            start_of_week = ref_date - _datetime.timedelta(days=ref_date.weekday())
            end = start_of_week - _datetime.timedelta(days=1)
            start = end - _datetime.timedelta(days=6)
        else:
            period_months = self._period_months()
            if period_months:
                start, end = self._calculate_month_period(ref_date, period_months)
            else:
                raise ValueError("calculate_period called for non-recurring schedule")

        return start, end

    def _advance_period(
        self, start: datetime_date, end: datetime_date
    ) -> tuple[datetime_date, datetime_date]:
        import calendar as _calendar
        import datetime as _datetime

        if self.periodicity == self.PERIODICITY_DAILY:
            delta = _datetime.timedelta(days=1)
            return start + delta, end + delta
        if self.periodicity == self.PERIODICITY_WEEKLY:
            delta = _datetime.timedelta(days=7)
            return start + delta, end + delta
        period_months = self._period_months()
        if period_months:
            base_start = start.replace(day=1)
            next_start = self._add_months(base_start, period_months)
            next_end_start = self._add_months(next_start, period_months)
            next_end = next_end_start - _datetime.timedelta(days=1)
            return next_start, next_end
        raise ValueError("advance_period called for non-recurring schedule")

    def _period_months(self) -> int | None:
        return {
            self.PERIODICITY_MONTHLY: 1,
            self.PERIODICITY_BIMONTHLY: 2,
            self.PERIODICITY_QUARTERLY: 3,
            self.PERIODICITY_YEARLY: 12,
        }.get(self.periodicity)

    def _calculate_month_period(
        self, ref_date: datetime_date, months: int
    ) -> tuple[datetime_date, datetime_date]:
        import calendar as _calendar
        import datetime as _datetime

        first_of_month = ref_date.replace(day=1)
        end = first_of_month - _datetime.timedelta(days=1)

        months_into_block = (end.month - 1) % months + 1
        if months_into_block < months:
            end_anchor = self._add_months(end.replace(day=1), -months_into_block)
            end_day = _calendar.monthrange(end_anchor.year, end_anchor.month)[1]
            end = end_anchor.replace(day=end_day)

        start_anchor = self._add_months(end.replace(day=1), -(months - 1))
        start = start_anchor.replace(day=1)
        return start, end

    @staticmethod
    def _add_months(base: datetime_date, months: int) -> datetime_date:
        import calendar as _calendar

        month_index = base.month - 1 + months
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        last_day = _calendar.monthrange(year, month)[1]
        day = min(base.day, last_day)
        return base.replace(year=year, month=month, day=day)

    def iter_pending_periods(self, reference=None):
        if self.periodicity == self.PERIODICITY_NONE:
            return []

        ref_date = reference or timezone.localdate()
        try:
            target_start, target_end = self.calculate_period(reference=ref_date)
        except ValueError:
            return []

        reports = self.reports.order_by("start_date", "end_date")
        last_report = reports.last()
        if last_report:
            current_start, current_end = self._advance_period(
                last_report.start_date, last_report.end_date
            )
        else:
            current_start, current_end = target_start, target_end

        if current_end < current_start:
            return []

        pending: list[tuple[datetime_date, datetime_date]] = []
        safety = 0
        while current_end <= target_end:
            exists = reports.filter(
                start_date=current_start, end_date=current_end
            ).exists()
            if not exists:
                pending.append((current_start, current_end))
            try:
                current_start, current_end = self._advance_period(
                    current_start, current_end
                )
            except ValueError:
                break
            safety += 1
            if safety > 400:
                break

        return pending

    def resolve_recipients(self):
        """Return (to, cc) email lists respecting owner fallbacks."""
        return resolve_recipient_fallbacks(
            self.email_recipients,
            owner=self.owner,
            include_owner_cc=True,
        )

    def resolve_reply_to(self) -> list[str]:
        from .reporting import ClientReport

        return ClientReport.resolve_reply_to_for_owner(self.owner)

    def get_outbox(self):
        """Return the preferred :class:`apps.emails.models.EmailOutbox` instance."""

        from .reporting import ClientReport

        return ClientReport.resolve_outbox_for_owner(self.owner)

    def notify_failure(self, message: str):
        from apps.nodes.models import NetMessage

        NetMessage.broadcast("Client report delivery issue", message)

    def run(self, *, start: datetime_date | None = None, end: datetime_date | None = None):
        """Generate the report, persist it and deliver notifications."""

        from .reporting import ClientReport

        if start is None or end is None:
            try:
                start, end = self.calculate_period()
            except ValueError:
                return None

        try:
            report = ClientReport.generate(
                start,
                end,
                owner=self.owner,
                schedule=self,
                recipients=self.email_recipients,
                disable_emails=self.disable_emails,
                chargers=list(self.chargers.all()),
                language=self.language,
                title=self.title,
            )
            report.chargers.set(self.chargers.all())
            report.store_local_copy()
        except Exception as exc:
            self.notify_failure(str(exc))
            raise

        if not self.disable_emails:
            to, cc = self.resolve_recipients()
            if not to:
                self.notify_failure("No recipients available for client report")
                raise RuntimeError("No recipients available for client report")
            else:
                try:
                    delivered = report.send_delivery(
                        to=to,
                        cc=cc,
                        outbox=self.get_outbox(),
                        reply_to=self.resolve_reply_to(),
                    )
                    if delivered:
                        type(report).objects.filter(pk=report.pk).update(
                            recipients=delivered
                        )
                        report.recipients = delivered
                except Exception as exc:
                    self.notify_failure(str(exc))
                    raise

        now = timezone.now()
        type(self).objects.filter(pk=self.pk).update(last_generated_on=now)
        self.last_generated_on = now
        return report

    def generate_missing_reports(self, reference=None):
        from .transactions import generate_missing_reports

        return generate_missing_reports(self, reference=reference)
