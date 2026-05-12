from __future__ import annotations

from celery import shared_task

from apps.users.error_report_analysis import analyze_error_report_package, redact_analysis_payload
from apps.users.models import UploadedErrorReport


@shared_task
def analyze_uploaded_error_report(report_id: int) -> None:
    report = UploadedErrorReport.objects.filter(pk=report_id).first()
    if report is None:
        return
    report.status = UploadedErrorReport.Status.PROCESSING
    report.error = ""
    report.save(update_fields=["status", "error", "updated_at"])
    try:
        result = analyze_error_report_package(report.package.path)
    except Exception as exc:
        report.status = UploadedErrorReport.Status.FAILED
        report.error = str(exc)
        report.save(update_fields=["status", "error", "updated_at"])
        return
    report.analysis = redact_analysis_payload(result)
    report.status = UploadedErrorReport.Status.COMPLETE
    report.save(update_fields=["analysis", "status", "updated_at"])
