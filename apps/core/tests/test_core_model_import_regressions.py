"""Regression tests for core model and admin import stability."""

from importlib import import_module

import pytest


@pytest.mark.django_db
def test_core_models_export_admin_notice_regression():
    """Regression: ``apps.core.models`` keeps ``AdminNotice`` available for imports."""
    core_models = import_module("apps.core.models")

    assert hasattr(core_models, "AdminNotice")


@pytest.mark.django_db
def test_admin_notice_admin_module_imports_regression():
    """Regression: admin autodiscovery can import the admin notice admin module."""
    module = import_module("apps.core.admin.admin_notice_admin")

    assert hasattr(module, "AdminNoticeAdmin")
