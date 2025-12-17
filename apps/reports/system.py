from __future__ import annotations

import logging
import re
from datetime import timedelta
from time import perf_counter
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db import DatabaseError, connections
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.reports.models import SQLReport
from apps.sigils.sigil_resolver import resolve_sigils

logger = logging.getLogger(__name__)


def _database_choices() -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []

    for alias, config in settings.DATABASES.items():
        label_parts: list[str] = []
        engine = str(config.get("ENGINE", ""))
        name = config.get("NAME")

        if engine:
            label_parts.append(engine.rsplit(".", 1)[-1])
        if name:
            label_parts.append(str(name))

        label = alias
        if label_parts:
            label = f"{alias} ({', '.join(label_parts)})"
        choices.append((alias, label))

    return choices or [("default", "default")]


class SQLReportForm(forms.ModelForm):
    report_id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = SQLReport
        fields = ["name", "database_alias", "query"]
        labels = {
            "name": _("Name"),
            "database_alias": _("Database"),
            "query": _("SQL query"),
        }
        help_texts = {
            "query": _(
                "Sigils will be resolved before executing the query against the database."
            ),
        }
        widgets = {
            "query": forms.Textarea(
                attrs={"rows": 12, "spellcheck": "false", "class": "vLargeTextField"}
            ),
        }

    def __init__(self, *args, database_choices: list[tuple[str, str]] | None = None, **kwargs):
        instance = kwargs.get("instance")
        super().__init__(*args, **kwargs)

        choices = database_choices or [(alias, alias) for alias in settings.DATABASES.keys()]
        self.fields["database_alias"].widget = forms.Select(choices=choices)
        if not self.fields["database_alias"].initial and choices:
            self.fields["database_alias"].initial = choices[0][0]

        if instance and instance.pk:
            self.fields["report_id"].initial = instance.pk

    def clean_database_alias(self):
        alias = self.cleaned_data.get("database_alias") or "default"
        if alias not in settings.DATABASES:
            raise forms.ValidationError(_("Unknown database alias."))
        return alias


def _execute_sql_report_query(resolved_sql: str, database_alias: str) -> dict[str, Any]:
    executed_at = timezone.now()
    alias = database_alias if database_alias in connections else "default"

    result: dict[str, Any] = {
        "columns": [],
        "rows": [],
        "row_count": 0,
        "resolved_sql": resolved_sql,
        "database_alias": alias,
        "executed_at": executed_at,
        "duration_ms": None,
        "error": None,
    }

    try:
        with connections[alias].cursor() as cursor:
            started = perf_counter()
            cursor.execute(resolved_sql)
            duration_seconds = perf_counter() - started

            result["duration_ms"] = duration_seconds * 1000

            if cursor.description:
                result["columns"] = [col[0] for col in cursor.description]
                result["rows"] = cursor.fetchall()
                result["row_count"] = len(result["rows"])
            else:
                rowcount = cursor.rowcount
                if rowcount and rowcount > 0:
                    result["row_count"] = rowcount
    except DatabaseError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # pragma: no cover - unexpected failure
        logger.exception("Unexpected error executing SQL report")
        result["error"] = str(exc)

    return result


def _system_sql_report_view(request: HttpRequest):
    database_choices = _database_choices()
    report_id = request.GET.get("report") or request.POST.get("report_id")
    selected_report = None
    query_result = None

    if report_id:
        selected_report = SQLReport.objects.filter(pk=report_id).first()

    if request.method == "POST":
        validate_only = "validate_sigils" in request.POST

        form = SQLReportForm(
            request.POST,
            instance=selected_report,
            database_choices=database_choices,
        )

        if form.is_valid():
            if validate_only:
                sql_text = form.cleaned_data.get("query") or ""
                tokens = re.findall(r"\[([^\[\]]+)\]", sql_text)
                if not tokens:
                    messages.warning(request, _("No sigils found in the query."))
                else:
                    unresolved = [
                        token
                        for token in tokens
                        if resolve_sigils(f"[{token}]") == f"[{token}]"
                    ]
                    if unresolved:
                        messages.error(
                            request,
                            _("Unable to resolve sigils: %(sigils)s")
                            % {"sigils": ", ".join(unresolved)},
                        )
                    else:
                        messages.success(
                            request,
                            _("All sigils resolved successfully."),
                        )
                if selected_report and selected_report.pk:
                    selected_report.refresh_from_db()
            else:
                sql_report = form.save()

                resolved_sql = resolve_sigils(sql_report.query)
                query_result = _execute_sql_report_query(resolved_sql, sql_report.database_alias)
                selected_report = sql_report

                if query_result.get("error"):
                    messages.error(
                        request,
                        _("Unable to run the SQL report: %(error)s")
                        % {"error": query_result["error"]},
                    )
                else:
                    SQLReport.objects.filter(pk=sql_report.pk).update(
                        last_run_at=query_result["executed_at"],
                        last_run_duration=timedelta(
                            milliseconds=query_result.get("duration_ms") or 0
                        ),
                        updated_at=timezone.now(),
                    )
                    sql_report.refresh_from_db(fields=["last_run_at", "last_run_duration"])
                    messages.success(request, _("Query executed successfully."))
        else:
            query_result = None
    else:
        form = SQLReportForm(
            instance=selected_report,
            database_choices=database_choices,
        )

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("SQL Report"),
            "sql_reports": SQLReport.objects.all(),
            "selected_report": selected_report,
            "sql_report_form": form,
            "query_result": query_result,
        }
    )
    return TemplateResponse(request, "admin/system_sql_report.html", context)


def patch_admin_sql_report_view() -> None:
    """Add the SQL report admin view."""

    original_get_urls = admin.site.get_urls
    if getattr(original_get_urls, "_reports_sql_report_patch", False):
        return

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "system/sql-report/",
                admin.site.admin_view(_system_sql_report_view),
                name="system-sql-report",
            ),
        ]
        return custom + urls

    get_urls._reports_sql_report_patch = True  # type: ignore[attr-defined]
    admin.site.get_urls = get_urls
