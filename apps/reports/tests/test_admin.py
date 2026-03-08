from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from apps.reports.admin import SQLReportProductAdmin
from apps.reports.models import SQLReportProduct


def test_sql_report_product_admin_has_no_add_permission():
    """SQLReportProduct is read-only in admin and cannot be added manually."""

    model_admin = SQLReportProductAdmin(SQLReportProduct, AdminSite())
    request = RequestFactory().get("/admin/reports/sqlreportproduct/add/")

    assert model_admin.has_add_permission(request) is False
