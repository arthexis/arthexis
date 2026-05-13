from __future__ import annotations

import logging
from pathlib import Path

from celery import shared_task

from apps.users.error_report_analysis import (
    analyze_error_report_package,
    redact_analysis_payload,
)
from apps.users.models import UploadedErrorReport

logger = logging.getLogger(__name__)


def _mark_report_failed(report: UploadedErrorReport, exc: Exception) -> None:
    report.status = UploadedErrorReport.Status.FAILED
    report.error = str(exc)
    report.save(update_fields=["status", "error", "updated_at"])


@shared_task
def analyze_uploaded_error_report(report_id: int) -> None:
    report = UploadedErrorReport.objects.filter(pk=report_id).first()
    if report is None:
        return
    report.status = UploadedErrorReport.Status.PROCESSING
    report.error = ""
    report.save(update_fields=["status", "error", "updated_at"])
    try:
        result = analyze_error_report_package(Path(report.package.path))
        analysis = redact_analysis_payload(result)
    except (FileNotFoundError, ValueError) as exc:
        _mark_report_failed(report, exc)
        return
    except Exception as exc:
        _mark_report_failed(report, exc)
        logger.exception("Unexpected error analyzing uploaded error report %s.", report.pk)
        raise
    report.analysis = analysis
    report.status = UploadedErrorReport.Status.COMPLETE
    report.save(update_fields=["analysis", "status", "updated_at"])
