from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.db import models
from django.utils import formats, timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext, gettext_lazy as _, override

from apps.core.entity import Entity
from apps.locale.language import (
    default_report_language,
    normalize_report_language,
    normalize_report_title,
)

logger = logging.getLogger(__name__)


class ClientReport(Entity):
    """Snapshot of energy usage over a period."""

    start_date = models.DateField()
    end_date = models.DateField()
    created_on = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(default=dict)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_reports",
    )
    schedule = models.ForeignKey(
        "energy.ClientReportSchedule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
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
        verbose_name=_("Title"),
    )
    recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)
    chargers = models.ManyToManyField(
        "ocpp.Charger",
        blank=True,
        related_name="client_reports",
    )

    class Meta:
        verbose_name = _("Consumer Report")
        verbose_name_plural = _("Consumer Reports")
        db_table = "core_client_report"
        ordering = ["-created_on"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        period_type = getattr(self.schedule, "periodicity", "none")
        return f"{self.start_date} - {self.end_date} ({period_type})"

    @staticmethod
    def default_language() -> str:
        return default_report_language()

    @staticmethod
    def normalize_language(language: str | None) -> str:
        return normalize_report_language(language)

    @staticmethod
    def normalize_title(title: str | None) -> str:
        return normalize_report_title(title)

    def save(self, *args, **kwargs):
        if self.language:
            self.language = normalize_report_language(self.language)
        self.title = self.normalize_title(self.title)
        super().save(*args, **kwargs)

    @property
    def periodicity_label(self) -> str:
        if self.schedule:
            return self.schedule.get_periodicity_display()
        from .scheduling import ClientReportSchedule

        return ClientReportSchedule.label_for_periodicity(
            ClientReportSchedule.PERIODICITY_NONE
        )

    @property
    def total_kw_period(self) -> float:
        totals = (self.rows_for_display or {}).get("totals", {})
        return float(totals.get("total_kw_period", 0.0) or 0.0)

    @classmethod
    def generate(
        cls,
        start_date,
        end_date,
        *,
        owner=None,
        schedule=None,
        recipients: list[str] | None = None,
        disable_emails: bool = False,
        chargers=None,
        language: str | None = None,
        title: str | None = None,
    ):
        from collections.abc import Iterable as _Iterable

        charger_list = []
        if chargers:
            if isinstance(chargers, _Iterable):
                charger_list = list(chargers)
            else:
                charger_list = [chargers]

        payload = cls.build_rows(start_date, end_date, chargers=charger_list)
        normalized_language = cls.normalize_language(language)
        title_value = cls.normalize_title(title)
        report = cls.objects.create(
            start_date=start_date,
            end_date=end_date,
            data=payload,
            owner=owner,
            schedule=schedule,
            recipients=list(recipients or []),
            disable_emails=disable_emails,
            language=normalized_language,
            title=title_value,
        )
        if charger_list:
            report.chargers.set(charger_list)
        return report

    def store_local_copy(self, html: str | None = None):
        """Persist the report data and optional HTML rendering to disk."""

        import json as _json
        from django.template.loader import render_to_string

        base_dir = Path(settings.BASE_DIR)
        report_dir = base_dir / "work" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        identifier = f"client_report_{self.pk}_{timestamp}"

        language_code = self.normalize_language(self.language)
        context = {
            "report": self,
            "language_code": language_code,
            "default_language": type(self).default_language(),
        }
        with override(language_code):
            html_content = html or render_to_string(
                "core/reports/client_report_email.html", context
            )
        html_path = report_dir / f"{identifier}.html"
        html_path.write_text(html_content, encoding="utf-8")

        json_path = report_dir / f"{identifier}.json"
        json_path.write_text(
            _json.dumps(self.data, indent=2, default=str), encoding="utf-8"
        )

        pdf_path = report_dir / f"{identifier}.pdf"
        self.render_pdf(pdf_path)

        export = {
            "html_path": ClientReport._relative_to_base(html_path, base_dir),
            "json_path": ClientReport._relative_to_base(json_path, base_dir),
            "pdf_path": ClientReport._relative_to_base(pdf_path, base_dir),
        }

        updated = dict(self.data)
        updated["export"] = export
        type(self).objects.filter(pk=self.pk).update(data=updated)
        self.data = updated
        return export, html_content

    def send_delivery(
        self,
        *,
        to: list[str] | tuple[str, ...],
        cc: list[str] | tuple[str, ...] | None = None,
        outbox=None,
        reply_to: list[str] | None = None,
    ) -> list[str]:
        from apps.emails import mailer

        recipients = list(to or [])
        if not recipients:
            return []

        pdf_path = self.ensure_pdf()
        attachments = [
            (pdf_path.name, pdf_path.read_bytes(), "application/pdf"),
        ]

        language_code = self.normalize_language(self.language)
        with override(language_code):
            totals = self.rows_for_display.get("totals", {})
            start_display = formats.date_format(
                self.start_date, format="DATE_FORMAT", use_l10n=True
            )
            end_display = formats.date_format(
                self.end_date, format="DATE_FORMAT", use_l10n=True
            )
            total_kw_period_label = gettext("Total kW during period")
            total_kw_all_label = gettext("Total kW (all time)")
            report_title = self.normalize_title(self.title) or gettext(
                "Consumer Report"
            )
            body_lines = [
                gettext("%(title)s for %(start)s through %(end)s.")
                % {"title": report_title, "start": start_display, "end": end_display},
                f"{total_kw_period_label}: "
                f"{formats.number_format(totals.get('total_kw_period', 0.0), decimal_pos=2, use_l10n=True)}.",
                f"{total_kw_all_label}: "
                f"{formats.number_format(totals.get('total_kw', 0.0), decimal_pos=2, use_l10n=True)}.",
            ]
            message = "\n".join(body_lines)
            subject = gettext("%(title)s %(start)s - %(end)s") % {
                "title": report_title,
                "start": start_display,
                "end": end_display,
            }

        kwargs = {}
        if reply_to:
            kwargs["reply_to"] = reply_to

        mailer.send(
            subject,
            message,
            recipients,
            outbox=outbox,
            cc=list(cc or []),
            attachments=attachments,
            **kwargs,
        )

        delivered = list(dict.fromkeys(recipients + list(cc or [])))
        return delivered

    @staticmethod
    def build_rows(
        start_date=None,
        end_date=None,
        *,
        for_display: bool = False,
        chargers=None,
    ):
        dataset = ClientReport._build_dataset(start_date, end_date, chargers=chargers)
        if for_display:
            return ClientReport._normalize_dataset_for_display(dataset)
        return dataset

    @staticmethod
    def _build_dataset(start_date=None, end_date=None, *, chargers=None):
        from datetime import datetime, time, timedelta, timezone as pytimezone
        from apps.ocpp.models import Transaction, annotate_transaction_energy_bounds

        Charger = apps.get_model("ocpp", "Charger")
        RFID = apps.get_model("cards", "RFID")

        qs = Transaction.objects.all()

        start_dt = None
        end_dt = None
        if start_date:
            start_dt = datetime.combine(start_date, time.min, tzinfo=pytimezone.utc)
            qs = qs.filter(start_time__gte=start_dt)
        if end_date:
            end_dt = datetime.combine(
                end_date + timedelta(days=1), time.min, tzinfo=pytimezone.utc
            )
            qs = qs.filter(start_time__lt=end_dt)

        selected_base_ids = None
        if chargers:
            selected_base_ids = {
                charger.charger_id for charger in chargers if charger.charger_id
            }
            if selected_base_ids:
                qs = qs.filter(charger__charger_id__in=selected_base_ids)

        qs = qs.select_related("account", "charger")
        qs = annotate_transaction_energy_bounds(
            qs,
            start_field="report_meter_energy_start",
            end_field="report_meter_energy_end",
        )
        transactions = list(qs.order_by("start_time", "pk"))

        rfid_values = {tx.rfid for tx in transactions if tx.rfid}
        tag_map: dict[str, RFID] = {}
        if rfid_values:
            tag_map = {
                tag.rfid: tag
                for tag in RFID.objects.filter(rfid__in=rfid_values).prefetch_related(
                    "energy_accounts"
                )
            }

        charger_ids = {
            tx.charger.charger_id
            for tx in transactions
            if getattr(tx, "charger", None) and tx.charger.charger_id
        }
        aggregator_map: dict[str, Charger] = {}
        if charger_ids:
            aggregator_map = {
                charger.charger_id: charger
                for charger in Charger.objects.filter(
                    charger_id__in=charger_ids, connector_id__isnull=True
                )
            }

        groups: dict[str, dict[str, Any]] = {}
        for tx in transactions:
            charger = getattr(tx, "charger", None)
            if charger is None:
                continue
            base_id = charger.charger_id
            if selected_base_ids is not None and base_id not in selected_base_ids:
                continue
            aggregator = aggregator_map.get(base_id) or charger
            entry = groups.setdefault(
                base_id,
                {"charger": aggregator, "transactions": []},
            )
            entry["transactions"].append(tx)

        evcs_entries: list[dict[str, Any]] = []
        total_all_time = 0.0
        total_period = 0.0

        def _sort_key(tx):
            anchor = getattr(tx, "start_time", None)
            if anchor is None:
                anchor = datetime.min.replace(tzinfo=pytimezone.utc)
            return (anchor, tx.pk or 0)

        for base_id, info in sorted(groups.items(), key=lambda item: item[0]):
            aggregator = info["charger"]
            txs = sorted(info["transactions"], key=_sort_key)
            total_kw_all = float(getattr(aggregator, "total_kw", 0.0) or 0.0)
            total_kw_period = 0.0
            if hasattr(aggregator, "total_kw_for_range"):
                total_kw_period = float(
                    aggregator.total_kw_for_range(start=start_dt, end=end_dt) or 0.0
                )
            total_all_time += total_kw_all
            total_period += total_kw_period

            session_rows: list[dict[str, Any]] = []
            for tx in txs:
                session_kw = float(getattr(tx, "kw", 0.0) or 0.0)
                if session_kw <= 0:
                    continue

                start_kwh, end_kwh = ClientReport._resolve_meter_bounds(tx)

                connector_number = (
                    tx.connector_id
                    if getattr(tx, "connector_id", None) is not None
                    else getattr(getattr(tx, "charger", None), "connector_id", None)
                )
                connector_letter = (
                    Charger.connector_letter_from_value(connector_number)
                    if connector_number not in {None, ""}
                    else None
                )
                connector_order = (
                    connector_number if isinstance(connector_number, int) else None
                )

                rfid_value = (tx.rfid or "").strip()
                tag = tag_map.get(rfid_value)
                label = None
                account_name = (
                    tx.account.name
                    if tx.account and getattr(tx.account, "name", None)
                    else None
                )
                if tag:
                    label = tag.custom_label or str(tag.label_id)
                    if not account_name:
                        account = next(iter(tag.energy_accounts.all()), None)
                        if account and getattr(account, "name", None):
                            account_name = account.name
                elif rfid_value:
                    label = rfid_value

                session_rows.append(
                    {
                        "connector": connector_number,
                        "connector_label": connector_letter,
                        "connector_order": connector_order,
                        "rfid_label": label,
                        "account_name": account_name,
                        "start_kwh": start_kwh,
                        "end_kwh": end_kwh,
                        "session_kwh": session_kw,
                        "start": tx.start_time.isoformat()
                        if getattr(tx, "start_time", None)
                        else None,
                        "end": tx.stop_time.isoformat()
                        if getattr(tx, "stop_time", None)
                        else None,
                    }
                )

            evcs_entries.append(
                {
                    "charger_id": aggregator.pk,
                    "serial_number": aggregator.charger_id,
                    "display_name": aggregator.display_name
                    or aggregator.name
                    or aggregator.charger_id,
                    "total_kw": total_kw_all,
                    "total_kw_period": total_kw_period,
                    "transactions": session_rows,
                }
            )

        filters: dict[str, Any] = {}
        if selected_base_ids:
            filters["chargers"] = sorted(selected_base_ids)

        return {
            "schema": "evcs-session/v1",
            "evcs": evcs_entries,
            "totals": {
                "total_kw": total_all_time,
                "total_kw_period": total_period,
            },
            "filters": filters,
        }

    @staticmethod
    def _resolve_meter_bounds(tx) -> tuple[float | None, float | None]:
        def _convert(value):
            if value in {None, ""}:
                return None
            try:
                return float(value) / 1000.0
            except (TypeError, ValueError):
                return None

        start_value = _convert(getattr(tx, "meter_start", None))
        end_value = _convert(getattr(tx, "meter_stop", None))

        def _coerce_energy(value):
            if value in {None, ""}:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        if start_value is None:
            annotated_start = getattr(tx, "report_meter_energy_start", None)
            start_value = _coerce_energy(annotated_start)

        if end_value is None:
            annotated_end = getattr(tx, "report_meter_energy_end", None)
            end_value = _coerce_energy(annotated_end)

        if start_value is None or end_value is None:
            readings_manager = getattr(tx, "meter_values", None)
            if readings_manager is not None:
                qs = readings_manager.filter(energy__isnull=False).order_by("timestamp")
                if start_value is None:
                    first_energy = qs.values_list("energy", flat=True).first()
                    start_value = _coerce_energy(first_energy)
                if end_value is None:
                    last_energy = qs.order_by("-timestamp").values_list(
                        "energy", flat=True
                    ).first()
                    end_value = _coerce_energy(last_energy)

        return start_value, end_value

    @staticmethod
    def _format_session_datetime(value):
        if not value:
            return None
        localized = timezone.localtime(value)
        date_part = formats.date_format(
            localized, format="MONTH_DAY_FORMAT", use_l10n=True
        )
        time_part = formats.time_format(
            localized, format="TIME_FORMAT", use_l10n=True
        )
        return gettext("%(date)s, %(time)s") % {
            "date": date_part,
            "time": time_part,
        }

    @staticmethod
    def _calculate_duration_minutes(start, end):
        if not start or not end:
            return None
        total_seconds = (end - start).total_seconds()
        if total_seconds < 0:
            return None
        return int(round(total_seconds / 60.0))

    @staticmethod
    def _normalize_dataset_for_display(dataset: dict[str, Any]):
        schema = dataset.get("schema")
        if schema == "evcs-session/v1":
            from datetime import datetime

            evcs_entries: list[dict[str, Any]] = []
            for entry in dataset.get("evcs", []):
                normalized_rows: list[dict[str, Any]] = []
                for row in entry.get("transactions", []):
                    start_val = row.get("start")
                    end_val = row.get("end")

                    start_dt = None
                    if start_val:
                        start_dt = parse_datetime(start_val)
                        if start_dt and timezone.is_naive(start_dt):
                            start_dt = timezone.make_aware(start_dt, timezone.utc)

                    end_dt = None
                    if end_val:
                        end_dt = parse_datetime(end_val)
                        if end_dt and timezone.is_naive(end_dt):
                            end_dt = timezone.make_aware(end_dt, timezone.utc)

                    normalized_rows.append(
                        {
                            "connector": row.get("connector"),
                            "connector_label": row.get("connector_label"),
                            "connector_order": row.get("connector_order"),
                            "rfid_label": row.get("rfid_label"),
                            "account_name": row.get("account_name"),
                            "start_kwh": row.get("start_kwh"),
                            "end_kwh": row.get("end_kwh"),
                            "session_kwh": row.get("session_kwh"),
                            "start": start_dt,
                            "end": end_dt,
                            "start_display": ClientReport._format_session_datetime(
                                start_dt
                            ),
                            "end_display": ClientReport._format_session_datetime(
                                end_dt
                            ),
                            "duration_minutes": ClientReport._calculate_duration_minutes(
                                start_dt, end_dt
                            ),
                        }
                    )

                def _connector_sort_value(item):
                    order_value = item.get("connector_order")
                    if isinstance(order_value, int):
                        return order_value
                    connector_value = item.get("connector")
                    if isinstance(connector_value, int):
                        return connector_value
                    try:
                        return int(connector_value)
                    except (TypeError, ValueError):
                        return 0

                normalized_rows.sort(
                    key=lambda item: (
                        item["start"]
                        if item["start"] is not None
                        else datetime.min.replace(tzinfo=timezone.utc),
                        _connector_sort_value(item),
                    )
                )

                evcs_entries.append(
                    {
                        "display_name": entry.get("display_name")
                        or entry.get("serial_number")
                        or "Charge Point",
                        "serial_number": entry.get("serial_number"),
                        "total_kw": entry.get("total_kw", 0.0),
                        "total_kw_period": entry.get("total_kw_period", 0.0),
                        "transactions": normalized_rows,
                    }
                )

            totals = dataset.get("totals", {})
            return {
                "schema": schema,
                "evcs": evcs_entries,
                "totals": {
                    "total_kw": totals.get("total_kw", 0.0),
                    "total_kw_period": totals.get("total_kw_period", 0.0),
                },
                "filters": dataset.get("filters", {}),
            }

        if schema == "session-list/v1":
            parsed: list[dict[str, Any]] = []
            for row in dataset.get("rows", []):
                item = dict(row)
                start_val = row.get("start")
                end_val = row.get("end")

                if start_val:
                    start_dt = parse_datetime(start_val)
                    if start_dt and timezone.is_naive(start_dt):
                        start_dt = timezone.make_aware(start_dt, timezone.utc)
                    item["start"] = start_dt
                else:
                    start_dt = None
                    item["start"] = None

                if end_val:
                    end_dt = parse_datetime(end_val)
                    if end_dt and timezone.is_naive(end_dt):
                        end_dt = timezone.make_aware(end_dt, timezone.utc)
                    item["end"] = end_dt
                else:
                    end_dt = None
                    item["end"] = None

                item["start_display"] = ClientReport._format_session_datetime(start_dt)
                item["end_display"] = ClientReport._format_session_datetime(end_dt)
                item["duration_minutes"] = ClientReport._calculate_duration_minutes(
                    start_dt, end_dt
                )

                parsed.append(item)

            return {"schema": schema, "rows": parsed}

        return {
            "schema": schema,
            "rows": dataset.get("rows", []),
            "filters": dataset.get("filters", {}),
        }

    @staticmethod
    def build_evcs_summary_rows(dataset: dict[str, Any] | None):
        """Flatten EVCS session data for summarized presentations."""

        if not dataset or dataset.get("schema") != "evcs-session/v1":
            return []

        summary_rows: list[dict[str, Any]] = []
        for entry in dataset.get("evcs", []):
            if not isinstance(entry, dict):
                continue

            display_name = (
                entry.get("display_name")
                or entry.get("serial_number")
                or gettext("Charge Point")
            )
            serial_number = entry.get("serial_number")
            transactions = entry.get("transactions") or []
            if not isinstance(transactions, list):
                continue

            for row in transactions:
                if not isinstance(row, dict):
                    continue
                summary_rows.append(
                    {
                        "display_name": display_name,
                        "serial_number": serial_number,
                        "transaction": row,
                    }
                )

        return summary_rows

    @property
    def rows_for_display(self):
        data = self.data or {}
        return ClientReport._normalize_dataset_for_display(data)

    @staticmethod
    def _relative_to_base(path: Path, base_dir: Path) -> str:
        try:
            return str(path.relative_to(base_dir))
        except ValueError:
            return str(path)

    @classmethod
    def _load_pdf_template(cls, language_code: str | None) -> dict[str, str]:
        from django.template import TemplateDoesNotExist
        from django.template.loader import render_to_string

        candidates: list[str] = []

        requested = (language_code or "").strip().replace("_", "-")
        base_language = requested.split("-", 1)[0] if requested else ""
        normalized = cls.normalize_language(language_code)

        for code in (requested, base_language, normalized):
            if code:
                candidates.append(code)

        default_code = default_report_language()
        if default_code:
            candidates.append(default_code)

        candidates.append("en")

        for code in dict.fromkeys(candidates):
            template_name = f"core/reports/client_report_pdf/{code}.json"
            try:
                rendered = render_to_string(template_name)
            except TemplateDoesNotExist:
                continue
            if not rendered:
                continue
            try:
                data = json.loads(rendered)
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid client report PDF template %s", template_name, exc_info=True
                )
                continue
            if isinstance(data, dict):
                return data

        return {}

    @staticmethod
    def resolve_reply_to_for_owner(owner) -> list[str]:
        if not owner:
            return []
        try:
            inbox_model = apps.get_model("emails", "EmailInbox")
        except LookupError:
            inbox_model = None
        try:
            inbox = owner.get_profile(inbox_model) if inbox_model else None
        except Exception:  # pragma: no cover - defensive catch
            inbox = None
        if inbox and getattr(inbox, "username", ""):
            address = inbox.username.strip()
            if address:
                return [address]
        return []

    @staticmethod
    def resolve_outbox_for_owner(owner):
        from apps.nodes.models import Node

        try:
            outbox_model = apps.get_model("emails", "EmailOutbox")
        except LookupError:
            outbox_model = None

        if owner:
            try:
                outbox = owner.get_profile(outbox_model) if outbox_model else None
            except Exception:  # pragma: no cover - defensive catch
                outbox = None
            if outbox:
                return outbox

        node = Node.get_local()
        if node:
            return getattr(node, "email_outbox", None)
        return None

    def render_pdf(self, target: Path):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        dataset = self.rows_for_display
        schema = dataset.get("schema")

        language_code = self.normalize_language(self.language)
        with override(language_code):
            styles = getSampleStyleSheet()
            title_style = styles["Title"]
            subtitle_style = styles["Heading2"]
            normal_style = styles["BodyText"]
            emphasis_style = styles["Heading3"]

            document = SimpleDocTemplate(
                str(target_path),
                pagesize=landscape(letter),
                leftMargin=0.5 * inch,
                rightMargin=0.5 * inch,
                topMargin=0.6 * inch,
                bottomMargin=0.5 * inch,
            )

            story: list = []
            labels = self._load_pdf_template(language_code)

            def label(key: str, default: str) -> str:
                value = labels.get(key) if isinstance(labels, dict) else None
                if isinstance(value, str) and value.strip():
                    return value
                return gettext(default)

            report_title = self.normalize_title(self.title) or label(
                "title", "Consumer Report"
            )
            story.append(Paragraph(report_title, title_style))

            start_display = formats.date_format(
                self.start_date, format="DATE_FORMAT", use_l10n=True
            )
            end_display = formats.date_format(
                self.end_date, format="DATE_FORMAT", use_l10n=True
            )
            default_period_text = gettext("Period: %(start)s to %(end)s") % {
                "start": start_display,
                "end": end_display,
            }
            period_template = labels.get("period") if isinstance(labels, dict) else None
            if isinstance(period_template, str):
                try:
                    period_text = period_template.format(
                        start=start_display, end=end_display
                    )
                except (KeyError, IndexError, ValueError):
                    logger.warning(
                        "Invalid period template for client report PDF: %s",
                        period_template,
                    )
                    period_text = default_period_text
            else:
                period_text = default_period_text
            story.append(Paragraph(period_text, emphasis_style))
            story.append(Spacer(1, 0.25 * inch))

            total_kw_all_time_label = label("total_kw_all_time", "Total kW (all time)")
            total_kw_period_label = label("total_kw_period", "Total kW (period)")
            connector_label = label("connector", "Connector")
            account_label = label("account", "Account")
            session_kwh_label = label("session_kwh", "Session kW")
            session_start_label = label("session_start", "Session start")
            session_end_label = label("session_end", "Session end")
            time_label = label("time", "Time")
            rfid_label = label("rfid_label", "RFID label")
            no_sessions_period = label(
                "no_sessions_period",
                "No charging sessions recorded for the selected period.",
            )
            no_sessions_point = label(
                "no_sessions_point",
                "No charging sessions recorded for this charge point.",
            )
            no_structured_data = label(
                "no_structured_data",
                "No structured data is available for this report.",
            )
            report_totals_label = label("report_totals", "Report totals")
            total_kw_period_line = label(
                "total_kw_period_line", "Total kW during period"
            )
            charge_point_label = label("charge_point", "Charge Point")
            serial_template = (
                labels.get("charge_point_serial")
                if isinstance(labels, dict)
                else None
            )

            def format_datetime(value):
                if not value:
                    return "—"
                return ClientReport._format_session_datetime(value) or "—"

            def format_decimal(value):
                if value is None:
                    return "—"
                return formats.number_format(value, decimal_pos=2, use_l10n=True)

            def format_duration(value):
                if value is None:
                    return "—"
                return formats.number_format(value, decimal_pos=0, use_l10n=True)

            if schema == "evcs-session/v1":
                evcs_entries = dataset.get("evcs", [])
                if not evcs_entries:
                    story.append(Paragraph(no_sessions_period, normal_style))
                for index, evcs in enumerate(evcs_entries):
                    if index:
                        story.append(Spacer(1, 0.2 * inch))

                    display_name = evcs.get("display_name") or charge_point_label
                    serial_number = evcs.get("serial_number")
                    if serial_number:
                        if isinstance(serial_template, str):
                            try:
                                header_text = serial_template.format(
                                    name=display_name, serial=serial_number
                                )
                            except (KeyError, IndexError, ValueError):
                                header_text = serial_template
                        else:
                            header_text = gettext("%(name)s (Serial: %(serial)s)") % {
                                "name": display_name,
                                "serial": serial_number,
                            }
                    else:
                        header_text = display_name
                    story.append(Paragraph(header_text, subtitle_style))

                    metrics_text = (
                        f"{total_kw_all_time_label}: "
                        f"{format_decimal(evcs.get('total_kw', 0.0))} | "
                        f"{total_kw_period_label}: "
                        f"{format_decimal(evcs.get('total_kw_period', 0.0))}"
                    )
                    story.append(Paragraph(metrics_text, normal_style))
                    story.append(Spacer(1, 0.1 * inch))

                    transactions = evcs.get("transactions", [])
                    if transactions:
                        table_data = [
                            [
                                session_kwh_label,
                                session_start_label,
                                session_end_label,
                                time_label,
                                connector_label,
                                rfid_label,
                                account_label,
                            ]
                        ]

                        for row in transactions:
                            start_dt = row.get("start")
                            end_dt = row.get("end")
                            duration_value = row.get("duration_minutes")
                            table_data.append(
                                [
                                    format_decimal(row.get("session_kwh")),
                                    format_datetime(start_dt),
                                    format_datetime(end_dt),
                                    format_duration(duration_value),
                                    (
                                        row.get("connector_label")
                                        or row.get("connector")
                                    )
                                    if row.get("connector") is not None
                                    or row.get("connector_label")
                                    else "—",
                                    row.get("rfid_label") or "—",
                                    row.get("account_name") or "—",
                                ]
                            )

                        column_count = len(table_data[0])
                        col_width = document.width / column_count if column_count else None
                        table = Table(
                            table_data,
                            repeatRows=1,
                            colWidths=[col_width] * column_count if col_width else None,
                            hAlign="LEFT",
                        )
                        table.setStyle(
                            TableStyle(
                                [
                                    (
                                        "BACKGROUND",
                                        (0, 0),
                                        (-1, 0),
                                        colors.HexColor("#0f172a"),
                                    ),
                                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                    ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                                    (
                                        "ROWBACKGROUNDS",
                                        (0, 1),
                                        (-1, -1),
                                        [colors.whitesmoke, colors.HexColor("#eef2ff")],
                                    ),
                                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                    ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                                ]
                            )
                        )
                        story.append(table)
                    else:
                        story.append(Paragraph(no_sessions_point, normal_style))
            else:
                story.append(Paragraph(no_structured_data, normal_style))

            totals = dataset.get("totals") or {}
            story.append(Spacer(1, 0.3 * inch))
            story.append(Paragraph(report_totals_label, emphasis_style))
            story.append(
                Paragraph(
                    f"{total_kw_all_time_label}: "
                    f"{format_decimal(totals.get('total_kw', 0.0))}",
                    emphasis_style,
                )
            )
            story.append(
                Paragraph(
                    f"{total_kw_period_line}: "
                    f"{format_decimal(totals.get('total_kw_period', 0.0))}",
                    emphasis_style,
                )
            )

            document.build(story)

    def ensure_pdf(self) -> Path:
        base_dir = Path(settings.BASE_DIR)
        export = dict((self.data or {}).get("export") or {})
        pdf_relative = export.get("pdf_path")
        if pdf_relative:
            candidate = base_dir / pdf_relative
            if candidate.exists():
                return candidate

        report_dir = base_dir / "work" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        identifier = f"client_report_{self.pk}_{timestamp}"
        pdf_path = report_dir / f"{identifier}.pdf"
        self.render_pdf(pdf_path)

        export["pdf_path"] = ClientReport._relative_to_base(pdf_path, base_dir)
        updated = dict(self.data)
        updated["export"] = export
        type(self).objects.filter(pk=self.pk).update(data=updated)
        self.data = updated
        return pdf_path
