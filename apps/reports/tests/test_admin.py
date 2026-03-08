from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from apps.reports.admin import SQLReportAdmin, SQLReportProductAdmin
from apps.reports.models import SQLReport, SQLReportProduct


def test_sql_report_product_admin_has_no_add_permission():
    """SQLReportProduct is read-only in admin and cannot be added manually."""

    model_admin = SQLReportProductAdmin(SQLReportProduct, AdminSite())
    request = RequestFactory().get("/admin/reports/sqlreportproduct/add/")

    assert model_admin.has_add_permission(request) is False


def test_sql_report_product_admin_has_no_delete_permission():
    """SQLReportProduct entries are immutable from admin once created."""

    model_admin = SQLReportProductAdmin(SQLReportProduct, AdminSite())
    request = RequestFactory().post("/admin/reports/sqlreportproduct/1/delete/")

    assert model_admin.has_delete_permission(request) is False


def test_sql_report_product_inline_has_no_add_permission():
    """SQLReport admin inline must not allow manual SQLReportProduct creation."""

    model_admin = SQLReportAdmin(SQLReport, AdminSite())
    inline = model_admin.get_inline_instances(RequestFactory().get("/admin/reports/sqlreport/"))[0]

    assert inline.has_add_permission(RequestFactory().get("/admin/reports/sqlreport/")) is False
